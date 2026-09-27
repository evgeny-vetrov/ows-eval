"""Profile validation and compilation of OWS 1.0.3 documents in one walk.

Every rejected construct produces a `Diagnostic` with its own code. When there are no
errors the walk also yields the node graph, so validation and compilation can never
disagree about what a document means.
"""

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from ai.gena.services.fabula.engine.dsl.diagnostics import Diagnostic
from ai.gena.services.fabula.engine.dsl.graph import (
    DIRECTIVES,
    ROOT,
    CatchSpec,
    Correlation,
    EventFilterSpec,
    Expr,
    ListenSpec,
    Node,
    RetrySpec,
    StrategySpec,
    SwitchCase,
    Template,
    Timeout,
    WorkflowSpec,
    child_path,
)
from ai.gena.services.fabula.engine.dsl.parser import SourceMap, escape_pointer_token
from ai.gena.services.fabula.engine.model.catalog import CapabilityDescriptor, CapabilityRef, EventTypeDescriptor
from ai.gena.services.fabula.engine.model.durations import MAX_DURATION, DurationError, parse_duration
from ai.gena.services.fabula.engine.model.jsonvalue import dumps_canonical
from ai.gena.services.fabula.engine.ports.catalogs import CapabilityCatalog, EventCatalog
from ai.gena.services.fabula.engine.ports.expressions import (
    ExpressionDiagnostics,
    ExpressionEngine,
    expression_body,
    is_expression,
)
from ai.gena.services.fabula.engine.ports.schemas import SchemaValidator

SUPPORTED_DSL = ("1.0.0", "1.0.1", "1.0.2", "1.0.3")
BASE_VARIABLES = frozenset(("context", "input", "output", "task", "workflow", "runtime"))
RESERVED_VARIABLES = BASE_VARIABLES | {"secrets", "authorization"}
TASK_KINDS = ("do", "set", "switch", "raise", "wait", "call", "try", "for", "listen", "emit", "fork")
COMMON_KEYS = frozenset(("if", "input", "output", "export", "timeout", "then", "metadata"))
KIND_KEYS = {
    "do": {"do"},
    "set": {"set"},
    "switch": {"switch"},
    "raise": {"raise"},
    "wait": {"wait"},
    "call": {"call", "with"},
    "try": {"try", "catch"},
    "for": {"for", "while", "do"},
    "listen": {"listen", "foreach"},
    "emit": {"emit"},
    "fork": {"fork"},
}
FORBIDDEN_CALLS = ("http", "grpc", "openapi", "asyncapi")
FORBIDDEN_USE = {
    "extensions": "forbidden.use_extensions",
    "authentications": "forbidden.use_authentications",
    "secrets": "forbidden.use_secrets",
    "functions": "forbidden.use_functions",
    "catalogs": "forbidden.use_catalogs",
}
ENVELOPE_ATTRS = ("id", "type", "source", "subject", "time")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")
_DOC_NAME = re.compile(r"^[a-z0-9](-?[a-z0-9])*$")
_VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SIMPLE_PATH = re.compile(r"^\.[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


@dataclass(frozen=True)
class ProfileLimits:
    max_nodes: int = 300
    max_depth: int = 10
    max_duration: timedelta = MAX_DURATION


@dataclass
class CompileResult:
    diagnostics: list[Diagnostic]
    workflow: WorkflowSpec | None = None
    nodes: dict[str, Node] = field(default_factory=dict)
    capabilities: dict[str, CapabilityDescriptor] = field(default_factory=dict)
    events: dict[str, EventTypeDescriptor] = field(default_factory=dict)
    context_keys: tuple[str, ...] = ()


class Compiler:
    def __init__(
        self,
        *,
        expressions: ExpressionEngine,
        schemas: SchemaValidator,
        capabilities: CapabilityCatalog,
        events: EventCatalog,
        limits: ProfileLimits = ProfileLimits(),
    ):
        self.expressions = expressions
        self.schemas = schemas
        self.capability_catalog = capabilities
        self.event_catalog = events
        self.limits = limits

    def compile(self, document: Any, source_map: SourceMap) -> CompileResult:
        return _Walk(self, source_map).run(document)


class _Walk:
    def __init__(self, compiler: Compiler, source_map: SourceMap):
        self.c = compiler
        self.source_map = source_map
        self.diagnostics: list[Diagnostic] = []
        self.nodes: dict[str, Node] = {}
        self.node_count = 0
        self.capabilities: dict[str, CapabilityDescriptor] = {}
        self.events: dict[str, EventTypeDescriptor] = {}
        self.context_keys: dict[str, None] = {}
        self.use_errors: dict[str, Any] = {}
        self.use_retries: dict[str, Any] = {}
        self.use_timeouts: dict[str, Any] = {}

    # --- diagnostics -------------------------------------------------------------

    def error(self, code: str, message: str, pointer: str, node: str | None = None, severity: str = "error") -> None:
        line, column = self.source_map.locate(pointer)
        self.diagnostics.append(
            Diagnostic(code=code, message=message, severity=severity, pointer=pointer, node=node, line=line, column=column)
        )

    def is_map(self, value: Any, pointer: str, what: str, node: str | None = None) -> bool:
        if isinstance(value, dict):
            return True
        self.error("type.object_expected", f"{what} must be an object", pointer, node)
        return False

    def is_list(self, value: Any, pointer: str, what: str, node: str | None = None) -> bool:
        if isinstance(value, list):
            return True
        self.error("type.array_expected", f"{what} must be an array", pointer, node)
        return False

    def is_str(self, value: Any, pointer: str, what: str, node: str | None = None) -> bool:
        if isinstance(value, str):
            return True
        self.error("type.string_expected", f"{what} must be a string", pointer, node)
        return False

    def only_keys(self, value: dict, allowed: set | frozenset, pointer: str, what: str, node: str | None = None) -> None:
        for key in value:
            if key not in allowed:
                self.error("unknown.key", f"'{key}' is not allowed in {what}", f"{pointer}/{escape_pointer_token(key)}", node)

    # --- expressions ---------------------------------------------------------------

    def expr(self, text: Any, pointer: str, variables: frozenset, node: str | None) -> Expr | None:
        if not is_expression(text):
            self.error("expression.expected", "a runtime expression '${ ... }' is expected", pointer, node)
            return None
        compiled = self.c.expressions.compile(expression_body(text))
        if isinstance(compiled, ExpressionDiagnostics):
            self.error("expression.syntax", "; ".join(compiled.messages), pointer, node)
            return None
        for reference in compiled.variables:
            head = reference[1:].split(".", 1)[0]
            if head == "secrets":
                self.error("forbidden.secrets", "$secrets is not available in this profile", pointer, node)
            elif head not in variables:
                self.error("expression.unknown_variable", f"variable ${head} is not defined here", pointer, node)
            elif head == "context" and "." in reference[1:]:
                self.context_keys.setdefault(reference[1:].split(".")[1], None)
        return Expr(text=text, compiled=compiled, pointer=pointer)

    def template(self, value: Any, pointer: str, variables: frozenset, node: str | None) -> Template:
        if isinstance(value, str) and is_expression(value):
            expr = self.expr(value, pointer, variables, node)
            return Template("expr", expr=expr) if expr else Template("const", value=None)
        if isinstance(value, dict):
            items = tuple((key, self.template(item, f"{pointer}/{escape_pointer_token(key)}", variables, node)) for key, item in value.items())
            if all(item.is_const for _, item in items):
                return Template("const", value=value)
            return Template("object", items=items)
        if isinstance(value, list):
            items = tuple(self.template(item, f"{pointer}/{index}", variables, node) for index, item in enumerate(value))
            if all(item.is_const for item in items):
                return Template("const", value=value)
            return Template("array", items=items)
        return Template("const", value=value)

    def transform(self, value: Any, pointer: str, variables: frozenset, node: str | None) -> Template | None:
        if isinstance(value, (str, dict)):
            return self.template(value, pointer, variables, node)
        self.error("type.transform", "a transformation must be an expression or an object", pointer, node)
        return None

    # --- durations and schemas ---------------------------------------------------------

    def duration(self, value: Any, pointer: str, variables: frozenset, node: str | None, what: str) -> Timeout | None:
        if is_expression(value):
            return Timeout(expr=self.template(value, pointer, variables, node))
        try:
            parsed = parse_duration(value)
        except DurationError as exc:
            self.error("duration.invalid", f"{what}: {exc}", pointer, node)
            return None
        if parsed > self.c.limits.max_duration:
            self.error("limit.duration", f"{what} exceeds {self.c.limits.max_duration.days} days", pointer, node)
            return None
        return Timeout(after=parsed)

    def timeout(self, value: Any, pointer: str, variables: frozenset, node: str | None) -> Timeout | None:
        if isinstance(value, str) and not is_expression(value):
            if value not in self.use_timeouts:
                self.error("reference.unknown_timeout", f"timeout '{value}' is not defined in use.timeouts", pointer, node)
                return None
            return self.timeout(self.use_timeouts[value], f"/use/timeouts/{escape_pointer_token(value)}", variables, node)
        if not self.is_map(value, pointer, "timeout", node):
            return None
        self.only_keys(value, {"after"}, pointer, "timeout", node)
        if "after" not in value:
            self.error("timeout.missing_after", "timeout requires 'after'", pointer, node)
            return None
        return self.duration(value["after"], f"{pointer}/after", variables, node, "timeout")

    def schema(self, value: Any, pointer: str, node: str | None) -> dict | None:
        if not self.is_map(value, pointer, "schema", node):
            return None
        if "resource" in value:
            self.error("schema.external", "external schema resources are not supported; inline the schema document", f"{pointer}/resource", node)
            return None
        self.only_keys(value, {"format", "document"}, pointer, "schema", node)
        if value.get("format", "json") != "json":
            self.error("schema.format", "only 'json' schemas are supported", f"{pointer}/format", node)
            return None
        document = value.get("document")
        if not self.is_map(document, f"{pointer}/document", "schema document", node):
            return None
        problems = self.c.schemas.check_schema(document)
        for problem in problems:
            self.error("schema.invalid", problem, f"{pointer}/document", node)
        return None if problems else document

    def dataflow(self, body: dict, pointer: str, variables: frozenset, node: str | None, section: str, transform_key: str) -> tuple[Template | None, dict | None]:
        value = body.get(section)
        if value is None:
            return None, None
        section_pointer = f"{pointer}/{section}"
        if not self.is_map(value, section_pointer, section, node):
            return None, None
        self.only_keys(value, {transform_key, "schema"}, section_pointer, section, node)
        transform = self.transform(value[transform_key], f"{section_pointer}/{transform_key}", variables, node) if transform_key in value else None
        schema = self.schema(value["schema"], f"{section_pointer}/schema", node) if "schema" in value else None
        return transform, schema

    # --- document ----------------------------------------------------------------------

    def run(self, document: Any) -> CompileResult:
        if not self.is_map(document, "", "the document"):
            return CompileResult(self.diagnostics)
        self.only_keys(document, {"document", "input", "use", "do", "timeout", "output", "schedule", "evaluate"}, "", "the document")
        meta = self.document_meta(document.get("document"))
        self.use(document.get("use"))
        self.evaluate(document.get("evaluate"))
        variables = BASE_VARIABLES
        input_from, input_schema = self.dataflow(document, "", variables, None, "input", "from")
        output_as, output_schema = self.dataflow(document, "", variables, None, "output", "as")
        timeout = self.timeout(document["timeout"], "/timeout", variables, None) if "timeout" in document else None
        schedule_after = self.schedule(document.get("schedule"))
        if "do" not in document:
            self.error("document.missing_do", "the document requires a 'do' task list", "")
            children: tuple[str, ...] = ()
        else:
            children = self.task_list(document["do"], "/do", ROOT, "do", 1, variables)
        if self.node_count > self.c.limits.max_nodes:
            self.error("limit.node_count", f"the document has {self.node_count} tasks, the limit is {self.c.limits.max_nodes}", "/do")
        self.nodes[ROOT] = Node(path=ROOT, name="", kind="workflow", pointer="", parent=None, list_key=None, depth=0, children={"do": children}, compat_key="workflow")
        workflow = None
        if meta is not None:
            workflow = WorkflowSpec(
                namespace=meta["namespace"],
                name=meta["name"],
                version=meta["version"],
                dsl=meta["dsl"],
                input_schema=input_schema,
                input_from=input_from,
                output_as=output_as,
                output_schema=output_schema,
                timeout=timeout,
                schedule_after=schedule_after,
            )
        return CompileResult(
            diagnostics=self.diagnostics,
            workflow=workflow,
            nodes=self.nodes,
            capabilities=self.capabilities,
            events=self.events,
            context_keys=tuple(self.context_keys),
        )

    def document_meta(self, meta: Any) -> dict | None:
        if meta is None:
            self.error("document.missing_header", "the document requires a 'document' header", "")
            return None
        if not self.is_map(meta, "/document", "document header"):
            return None
        self.only_keys(meta, {"dsl", "namespace", "name", "version", "title", "summary", "tags", "metadata"}, "/document", "document header")
        valid = True
        for key in ("dsl", "namespace", "name", "version"):
            if key not in meta:
                self.error("document.missing_field", f"document.{key} is required", "/document")
                valid = False
            elif not self.is_str(meta[key], f"/document/{key}", f"document.{key}"):
                valid = False
        if isinstance(meta.get("dsl"), str) and meta["dsl"] not in SUPPORTED_DSL:
            self.error("document.dsl_version", f"dsl '{meta['dsl']}' is not supported, expected one of {', '.join(SUPPORTED_DSL)}", "/document/dsl")
        for key in ("namespace", "name"):
            if isinstance(meta.get(key), str) and not _DOC_NAME.match(meta[key]):
                self.error("document.name_format", f"document.{key} must match {_DOC_NAME.pattern}", f"/document/{key}")
        return meta if valid else None

    def use(self, use: Any) -> None:
        if use is None or not self.is_map(use, "/use", "use"):
            return
        for key in use:
            pointer = f"/use/{escape_pointer_token(key)}"
            if key in FORBIDDEN_USE:
                self.error(FORBIDDEN_USE[key], f"use.{key} is forbidden in this profile", pointer)
            elif key in ("errors", "retries", "timeouts"):
                if self.is_map(use[key], pointer, f"use.{key}"):
                    getattr(self, f"use_{key}").update(use[key])
            else:
                self.error("unknown.key", f"'{key}' is not allowed in use", pointer)

    def evaluate(self, value: Any) -> None:
        if value is None or not self.is_map(value, "/evaluate", "evaluate"):
            return
        self.only_keys(value, {"language", "mode"}, "/evaluate", "evaluate")
        if value.get("language", "jq") != "jq":
            self.error("evaluate.language", "only the 'jq' expression language is supported", "/evaluate/language")
        if value.get("mode", "strict") != "strict":
            self.error("evaluate.mode", "only the 'strict' evaluation mode is supported", "/evaluate/mode")

    def schedule(self, value: Any) -> timedelta | None:
        if value is None or not self.is_map(value, "/schedule", "schedule"):
            return None
        after = None
        for key in value:
            pointer = f"/schedule/{escape_pointer_token(key)}"
            if key in ("every", "cron"):
                self.error(f"forbidden.schedule_{key}", f"schedule.{key} is recurring; only a one-shot 'schedule.after' is allowed", pointer)
            elif key == "on":
                self.error("forbidden.schedule_on", "schedule.on (event-driven start) is not allowed; only 'schedule.after'", pointer)
            elif key == "after":
                parsed = self.duration(value["after"], pointer, BASE_VARIABLES, None, "schedule.after")
                if parsed and parsed.expr:
                    self.error("schedule.dynamic", "schedule.after must be a literal duration", pointer)
                elif parsed:
                    after = parsed.after
            else:
                self.error("unknown.key", f"'{key}' is not allowed in schedule", pointer)
        return after

    # --- tasks -----------------------------------------------------------------------------

    def task_list(self, items: Any, pointer: str, parent: str, list_key: str, depth: int, variables: frozenset) -> tuple[str, ...]:
        if not self.is_list(items, pointer, "a task list", parent):
            return ()
        if depth > self.c.limits.max_depth:
            self.error("limit.nesting_depth", f"tasks are nested deeper than {self.c.limits.max_depth} levels", pointer, parent)
            return ()
        names: list[str] = []
        entries = []
        for index, item in enumerate(items):
            item_pointer = f"{pointer}/{index}"
            if not isinstance(item, dict) or len(item) != 1:
                self.error("task.entry", "each task list entry must be an object with exactly one task name", item_pointer, parent)
                continue
            (name, body), = item.items()
            name_pointer = f"{item_pointer}/{escape_pointer_token(name)}"
            if name in DIRECTIVES:
                self.error("task.name_reserved", f"'{name}' is a flow directive and cannot name a task", name_pointer, parent)
                continue
            if not _NAME.match(name):
                self.error("task.name_format", f"task name '{name}' must match {_NAME.pattern}", name_pointer, parent)
                continue
            if name in names:
                self.error("task.duplicate_name", f"task name '{name}' is already used in this list", name_pointer, parent)
                continue
            names.append(name)
            entries.append((index, name, body, name_pointer))
        paths = []
        for index, name, body, name_pointer in entries:
            path = child_path(parent, list_key, name)
            if self.task(name, body, name_pointer, path, parent, list_key, depth, variables):
                paths.append(path)
        for path in paths:
            node = self.nodes[path]
            targets = [(node.then, f"{node.pointer}/then")] + [(case.then, f"{node.pointer}/switch") for case in node.cases]
            for target, target_pointer in targets:
                if list_key == "branches" and target != "continue":
                    self.error("then.fork_branch", "fork branches run side by side: a branch cannot use 'then'; put the directive inside the branch", target_pointer, path)
                elif target not in DIRECTIVES and target not in names:
                    self.error("then.unknown_target", f"'{target}' is neither a flow directive nor a task in the same list", target_pointer, path)
        return tuple(paths)

    def task(self, name: str, body: Any, pointer: str, path: str, parent: str, list_key: str, depth: int, variables: frozenset) -> bool:
        self.node_count += 1
        if not self.is_map(body, pointer, f"task '{name}'", path):
            return False
        if "run" in body:
            self.error("forbidden.run", "'run' tasks (containers, scripts, shells, workflows) are forbidden", f"{pointer}/run", path)
            return False
        kinds = [kind for kind in TASK_KINDS if kind in body and not (kind == "do" and "for" in body)]
        if len(kinds) != 1:
            if kinds:
                self.error("task.ambiguous_kind", f"task '{name}' mixes {', '.join(kinds)}", pointer, path)
            else:
                self.error("task.unknown_kind", f"task '{name}' has no known task type", pointer, path)
            return False
        kind = kinds[0]
        self.only_keys(body, COMMON_KEYS | KIND_KEYS[kind], pointer, f"a '{kind}' task", path)
        spec: dict[str, Any] = dict(path=path, name=name, kind=kind, pointer=pointer, parent=parent, list_key=list_key, depth=depth, compat_key=kind)
        if "if" in body:
            spec["condition"] = self.expr(body["if"], f"{pointer}/if", variables, path)
        spec["input_from"], spec["input_schema"] = self.dataflow(body, pointer, variables, path, "input", "from")
        spec["output_as"], spec["output_schema"] = self.dataflow(body, pointer, variables, path, "output", "as")
        spec["export_as"], spec["export_schema"] = self.dataflow(body, pointer, variables, path, "export", "as")
        if "timeout" in body:
            spec["timeout"] = self.timeout(body["timeout"], f"{pointer}/timeout", variables, path)
        if "then" in body and self.is_str(body["then"], f"{pointer}/then", "then", path):
            spec["then"] = body["then"]
        getattr(self, f"task_{kind}")(body, pointer, path, depth, variables, spec)
        self.nodes[path] = Node(**spec)
        return True

    def task_do(self, body, pointer, path, depth, variables, spec):
        spec["children"] = {"do": self.task_list(body["do"], f"{pointer}/do", path, "do", depth + 1, variables)}

    def task_set(self, body, pointer, path, depth, variables, spec):
        value = body["set"]
        if not (isinstance(value, dict) or is_expression(value)):
            self.error("set.type", "'set' must be an object or a runtime expression", f"{pointer}/set", path)
        spec["template"] = self.template(value, f"{pointer}/set", variables, path)

    def task_switch(self, body, pointer, path, depth, variables, spec):
        cases = []
        switch_pointer = f"{pointer}/switch"
        if not self.is_list(body["switch"], switch_pointer, "switch", path):
            return
        seen: set[str] = set()
        defaults = 0
        for index, item in enumerate(body["switch"]):
            item_pointer = f"{switch_pointer}/{index}"
            if not isinstance(item, dict) or len(item) != 1:
                self.error("switch.case", "each switch entry must be an object with exactly one case name", item_pointer, path)
                continue
            (case_name, case), = item.items()
            case_pointer = f"{item_pointer}/{escape_pointer_token(case_name)}"
            if case_name in seen:
                self.error("switch.duplicate_case", f"case '{case_name}' is already defined", case_pointer, path)
            seen.add(case_name)
            if not self.is_map(case, case_pointer, f"case '{case_name}'", path):
                continue
            self.only_keys(case, {"when", "then"}, case_pointer, "a switch case", path)
            when = self.expr(case["when"], f"{case_pointer}/when", variables, path) if "when" in case else None
            if "when" not in case:
                defaults += 1
            then = case.get("then", "continue")
            if not self.is_str(then, f"{case_pointer}/then", "then", path):
                continue
            cases.append(SwitchCase(name=case_name, when=when, then=then))
        if defaults > 1:
            self.error("switch.multiple_defaults", "a switch may have only one case without 'when'", switch_pointer, path)
        spec["cases"] = tuple(cases)

    def task_raise(self, body, pointer, path, depth, variables, spec):
        raise_pointer = f"{pointer}/raise"
        if not self.is_map(body["raise"], raise_pointer, "raise", path):
            return
        self.only_keys(body["raise"], {"error"}, raise_pointer, "raise", path)
        error = body["raise"].get("error")
        error_pointer = f"{raise_pointer}/error"
        if isinstance(error, str) and not is_expression(error):
            if error not in self.use_errors:
                self.error("reference.unknown_error", f"error '{error}' is not defined in use.errors", error_pointer, path)
                return
            error, error_pointer = self.use_errors[error], f"/use/errors/{escape_pointer_token(error)}"
        if not self.is_map(error, error_pointer, "raise.error", path):
            return
        self.only_keys(error, {"type", "status", "title", "detail", "instance"}, error_pointer, "an error", path)
        for key in ("type", "status"):
            if key not in error:
                self.error("raise.missing_field", f"raise.error.{key} is required", error_pointer, path)
        if "status" in error and not is_expression(error["status"]) and (isinstance(error["status"], bool) or not isinstance(error["status"], int)):
            self.error("raise.status", "raise.error.status must be an integer", f"{error_pointer}/status", path)
        spec["template"] = self.template(error, error_pointer, variables, path)

    def task_wait(self, body, pointer, path, depth, variables, spec):
        value = body["wait"]
        wait_pointer = f"{pointer}/wait"
        if isinstance(value, dict) and "until" in value:
            self.only_keys(value, {"until"}, wait_pointer, "wait", path)
            spec["wait_until"] = self.template(value["until"], f"{wait_pointer}/until", variables, path)
            return
        spec["wait_for"] = self.duration(value, wait_pointer, variables, path, "wait")

    def task_call(self, body, pointer, path, depth, variables, spec):
        ref = body["call"]
        call_pointer = f"{pointer}/call"
        if not self.is_str(ref, call_pointer, "call", path):
            return
        if ref in FORBIDDEN_CALLS:
            self.error(f"forbidden.call_{ref}", f"'call: {ref}' is forbidden; only catalog capabilities 'name:version@provider' can be called", call_pointer, path)
            return
        if CapabilityRef.parse(ref) is None:
            self.error("call.ref_format", f"'{ref}' is not a capability reference 'name:version@provider'", call_pointer, path)
            return
        descriptor = self.capabilities.get(ref) or self.c.capability_catalog.resolve(ref)
        if descriptor is None:
            self.error("call.unknown_capability", f"capability '{ref}' is not in the catalog", call_pointer, path)
            return
        self.capabilities[ref] = descriptor
        arguments = body.get("with", {})
        if not (isinstance(arguments, dict) or is_expression(arguments)):
            self.error("call.with_type", "'with' must be an object or a runtime expression", f"{pointer}/with", path)
            arguments = {}
        template = self.template(arguments, f"{pointer}/with", variables, path)
        if template.is_const and descriptor.input_schema is not None:
            for problem in self.c.schemas.validate(descriptor.input_schema, template.value):
                self.error("call.input_schema", f"'with' does not match the input schema of {ref}: {problem}", f"{pointer}/with", path)
        spec.update(template=template, capability_ref=ref, capability=descriptor, compat_key=f"call:{descriptor.ref}")

    def task_try(self, body, pointer, path, depth, variables, spec):
        children = {"try": self.task_list(body["try"], f"{pointer}/try", path, "try", depth + 1, variables)}
        catch_pointer = f"{pointer}/catch"
        catch = body.get("catch")
        if catch is None:
            self.error("try.missing_catch", "'try' requires a 'catch' block", pointer, path)
        elif self.is_map(catch, catch_pointer, "catch", path):
            self.only_keys(catch, {"errors", "as", "when", "exceptWhen", "retry", "do"}, catch_pointer, "catch", path)
            as_name = catch.get("as", "error")
            if not isinstance(as_name, str) or not _VAR_NAME.match(as_name) or as_name in RESERVED_VARIABLES:
                self.error("catch.as", "catch.as must be a variable name that does not shadow a built-in one", f"{catch_pointer}/as", path)
                as_name = "error"
            scoped = variables | {as_name}
            errors_with = self.errors_filter(catch.get("errors"), f"{catch_pointer}/errors", path)
            when = self.expr(catch["when"], f"{catch_pointer}/when", scoped, path) if "when" in catch else None
            except_when = self.expr(catch["exceptWhen"], f"{catch_pointer}/exceptWhen", scoped, path) if "exceptWhen" in catch else None
            retry = self.retry(catch["retry"], f"{catch_pointer}/retry", scoped, path) if "retry" in catch else None
            if "do" in catch:
                children["catch"] = self.task_list(catch["do"], f"{catch_pointer}/do", path, "catch", depth + 1, scoped)
            spec["catch"] = CatchSpec(errors_with=errors_with, as_name=as_name, when=when, except_when=except_when, retry=retry)
        spec["children"] = children

    def errors_filter(self, value: Any, pointer: str, path: str) -> dict[str, Any]:
        if value is None or not self.is_map(value, pointer, "catch.errors", path):
            return {}
        self.only_keys(value, {"with"}, pointer, "catch.errors", path)
        with_ = value.get("with", {})
        if not self.is_map(with_, f"{pointer}/with", "catch.errors.with", path):
            return {}
        self.only_keys(with_, {"type", "status", "instance", "title", "detail"}, f"{pointer}/with", "catch.errors.with", path)
        return dict(with_)

    def retry(self, value: Any, pointer: str, variables: frozenset, path: str) -> RetrySpec | None:
        if isinstance(value, str):
            if value not in self.use_retries:
                self.error("reference.unknown_retry", f"retry policy '{value}' is not defined in use.retries", pointer, path)
                return None
            value, pointer = self.use_retries[value], f"/use/retries/{escape_pointer_token(value)}"
        if not self.is_map(value, pointer, "retry", path):
            return None
        self.only_keys(value, {"delay", "backoff", "limit", "jitter", "when", "exceptWhen"}, pointer, "retry", path)
        delay = self.static_duration(value.get("delay", "PT0S"), f"{pointer}/delay", path, "retry.delay")
        backoff = "constant"
        if "backoff" in value:
            strategy = value["backoff"]
            if not isinstance(strategy, dict) or len(strategy) != 1 or next(iter(strategy)) not in ("constant", "linear", "exponential"):
                self.error("retry.backoff", "retry.backoff must be one of {constant: {}}, {linear: {}}, {exponential: {}}", f"{pointer}/backoff", path)
            else:
                backoff = next(iter(strategy))
        limit = value.get("limit", {})
        count = attempt_duration = total_duration = None
        if self.is_map(limit, f"{pointer}/limit", "retry.limit", path):
            self.only_keys(limit, {"attempt", "duration"}, f"{pointer}/limit", "retry.limit", path)
            attempt = limit.get("attempt", {})
            if self.is_map(attempt, f"{pointer}/limit/attempt", "retry.limit.attempt", path):
                self.only_keys(attempt, {"count", "duration"}, f"{pointer}/limit/attempt", "retry.limit.attempt", path)
                if "count" in attempt:
                    count = attempt["count"]
                    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                        self.error("retry.limit_count", "retry.limit.attempt.count must be a non-negative integer", f"{pointer}/limit/attempt/count", path)
                        count = 0
                if "duration" in attempt:
                    attempt_duration = self.static_duration(attempt["duration"], f"{pointer}/limit/attempt/duration", path, "retry.limit.attempt.duration")
            if "duration" in limit:
                total_duration = self.static_duration(limit["duration"], f"{pointer}/limit/duration", path, "retry.limit.duration")
        if count is None and total_duration is None:
            self.error("retry.unbounded", "retry needs limit.attempt.count or limit.duration", pointer, path)
        jitter_from = jitter_to = timedelta(0)
        if "jitter" in value and self.is_map(value["jitter"], f"{pointer}/jitter", "retry.jitter", path):
            jitter = value["jitter"]
            self.only_keys(jitter, {"from", "to"}, f"{pointer}/jitter", "retry.jitter", path)
            jitter_from = self.static_duration(jitter.get("from", "PT0S"), f"{pointer}/jitter/from", path, "retry.jitter.from") or timedelta(0)
            jitter_to = self.static_duration(jitter.get("to", "PT0S"), f"{pointer}/jitter/to", path, "retry.jitter.to") or timedelta(0)
            if jitter_to < jitter_from:
                self.error("retry.jitter", "retry.jitter.to must not be less than retry.jitter.from", f"{pointer}/jitter", path)
        when = self.expr(value["when"], f"{pointer}/when", variables, path) if "when" in value else None
        except_when = self.expr(value["exceptWhen"], f"{pointer}/exceptWhen", variables, path) if "exceptWhen" in value else None
        return RetrySpec(
            delay=delay or timedelta(0),
            backoff=backoff,
            jitter_from=jitter_from,
            jitter_to=jitter_to,
            limit_count=count,
            limit_attempt_duration=attempt_duration,
            limit_duration=total_duration,
            when=when,
            except_when=except_when,
        )

    def static_duration(self, value: Any, pointer: str, path: str, what: str) -> timedelta | None:
        if is_expression(value):
            self.error("duration.dynamic", f"{what} must be a literal duration", pointer, path)
            return None
        parsed = self.duration(value, pointer, BASE_VARIABLES, path, what)
        return parsed.after if parsed else None

    def task_for(self, body, pointer, path, depth, variables, spec):
        loop = body["for"]
        loop_pointer = f"{pointer}/for"
        if not self.is_map(loop, loop_pointer, "for", path):
            return
        self.only_keys(loop, {"each", "in", "at"}, loop_pointer, "for", path)
        each, at = loop.get("each", "item"), loop.get("at", "index")
        for key, name in (("each", each), ("at", at)):
            if not isinstance(name, str) or not _VAR_NAME.match(name) or name in RESERVED_VARIABLES:
                self.error("for.variable", f"for.{key} must be a variable name that does not shadow ${name}", f"{loop_pointer}/{key}", path)
        if "in" not in loop:
            self.error("for.missing_in", "for.in is required", loop_pointer, path)
        else:
            spec["for_in"] = self.expr(loop["in"], f"{loop_pointer}/in", variables, path)
        scoped = variables | {each, at}
        if "while" in body:
            spec["for_while"] = self.expr(body["while"], f"{pointer}/while", scoped, path)
        if "do" not in body:
            self.error("for.missing_do", "a 'for' task requires 'do'", pointer, path)
        spec.update(
            for_each=each,
            for_at=at,
            children={"do": self.task_list(body.get("do", []), f"{pointer}/do", path, "do", depth + 1, scoped)},
        )

    def task_listen(self, body, pointer, path, depth, variables, spec):
        listen = body["listen"]
        listen_pointer = f"{pointer}/listen"
        if not self.is_map(listen, listen_pointer, "listen", path):
            return
        self.only_keys(listen, {"to", "read"}, listen_pointer, "listen", path)
        read = listen.get("read", "data")
        if read not in ("data", "envelope", "raw"):
            self.error("listen.read", "listen.read must be 'data', 'envelope' or 'raw'", f"{listen_pointer}/read", path)
        if "to" not in listen:
            self.error("listen.missing_to", "listen.to is required", listen_pointer, path)
            return
        strategy = self.strategy(listen["to"], f"{listen_pointer}/to", variables, path, allow_until=True)
        foreach = body.get("foreach")
        item, at, output_as, export_as = "item", "index", None, None
        children: dict[str, tuple[str, ...]] = {}
        if foreach is not None and self.is_map(foreach, f"{pointer}/foreach", "foreach", path):
            self.only_keys(foreach, {"item", "at", "do", "output", "export"}, f"{pointer}/foreach", "foreach", path)
            item, at = foreach.get("item", "item"), foreach.get("at", "index")
            for key, name in (("item", item), ("at", at)):
                if not isinstance(name, str) or not _VAR_NAME.match(name) or name in RESERVED_VARIABLES:
                    self.error("foreach.variable", f"foreach.{key} must be a variable name that does not shadow ${name}", f"{pointer}/foreach/{key}", path)
            scoped = variables | {item, at}
            children["foreach"] = self.task_list(foreach.get("do", []), f"{pointer}/foreach/do", path, "foreach", depth + 1, scoped)
            output_as, _ = self.dataflow(foreach, f"{pointer}/foreach", scoped, path, "output", "as")
            export_as, _ = self.dataflow(foreach, f"{pointer}/foreach", scoped, path, "export", "as")
        if strategy is None:
            return
        spec.update(
            children=children,
            listen=ListenSpec(
                strategy=strategy,
                read=read,
                foreach=foreach is not None,
                item=item,
                at=at,
                foreach_output_as=output_as,
                foreach_export_as=export_as,
            ),
            compat_key="listen:" + dumps_canonical({"listen": listen, "foreach": foreach is not None}),
        )

    def strategy(self, value: Any, pointer: str, variables: frozenset, path: str, allow_until: bool) -> StrategySpec | None:
        if not self.is_map(value, pointer, "listen.to", path):
            return None
        modes = [mode for mode in ("one", "any", "all") if mode in value]
        self.only_keys(value, {"one", "any", "all", "until"} if allow_until else {"one", "any", "all"}, pointer, "an event consumption strategy", path)
        if len(modes) != 1:
            self.error("listen.strategy", "exactly one of 'one', 'any' or 'all' is required", pointer, path)
            return None
        mode = modes[0]
        mode_pointer = f"{pointer}/{mode}"
        raw_filters = [value[mode]] if mode == "one" else value[mode]
        if mode != "one":
            if not self.is_list(raw_filters, mode_pointer, f"listen.to.{mode}", path):
                return None
            if not raw_filters:
                self.error("listen.empty_filters", f"listen.to.{mode} needs at least one event filter with a type", mode_pointer, path)
                return None
        filters = []
        for index, raw in enumerate(raw_filters):
            compiled = self.event_filter(raw, mode_pointer if mode == "one" else f"{mode_pointer}/{index}", variables, path)
            if compiled:
                filters.append(compiled)
        until_expr = until = None
        if "until" in value:
            until_pointer = f"{pointer}/until"
            if mode != "any":
                self.error("listen.until_requires_any", "'until' is only allowed with listen.to.any", until_pointer, path)
            elif isinstance(value["until"], str):
                until_expr = self.expr(value["until"], until_pointer, variables, path)
            else:
                until = self.strategy(value["until"], until_pointer, variables, path, allow_until=False)
        if len(filters) != len(raw_filters):
            return None
        return StrategySpec(mode=mode, filters=tuple(filters), until_expr=until_expr, until=until)

    def event_filter(self, value: Any, pointer: str, variables: frozenset, path: str) -> EventFilterSpec | None:
        if not self.is_map(value, pointer, "an event filter", path):
            return None
        self.only_keys(value, {"with", "correlate"}, pointer, "an event filter", path)
        properties = value.get("with")
        if not self.is_map(properties, f"{pointer}/with", "event filter 'with'", path):
            return None
        event_type = properties.get("type")
        if not isinstance(event_type, str) or is_expression(event_type):
            self.error("listen.filter_type", "every event filter needs a literal 'with.type'", f"{pointer}/with", path)
            return None
        descriptor = self.resolve_event(event_type, f"{pointer}/with/type", path)
        predicates: list[tuple[str, Template]] = []
        for key, item in properties.items():
            if key == "type":
                continue
            item_pointer = f"{pointer}/with/{escape_pointer_token(key)}"
            if key == "data" and isinstance(item, dict):
                for attr, leaf, leaf_pointer in _flatten("data", item, item_pointer):
                    predicates.append((attr, self.template(leaf, leaf_pointer, variables, path)))
            else:
                attr = key if key in ENVELOPE_ATTRS or key == "data" else f"attributes.{key}"
                predicates.append((attr, self.template(item, item_pointer, variables, path)))
        correlations = []
        correlate = value.get("correlate", {})
        if self.is_map(correlate, f"{pointer}/correlate", "correlate", path):
            for key, rule in correlate.items():
                rule_pointer = f"{pointer}/correlate/{escape_pointer_token(key)}"
                if not self.is_map(rule, rule_pointer, f"correlation '{key}'", path):
                    continue
                self.only_keys(rule, {"from", "expect"}, rule_pointer, "a correlation", path)
                source = rule.get("from")
                body = expression_body(source) if is_expression(source) else None
                if body is None or not _SIMPLE_PATH.match(body):
                    self.error("listen.correlation_from", "correlate.from must be a simple path expression such as '${ .data.id }'", f"{rule_pointer}/from", path)
                    continue
                if "expect" not in rule:
                    self.error("listen.correlation_expect", "correlate.expect is required: correlation values are fixed when the wait is registered", rule_pointer, path)
                    continue
                correlations.append(Correlation(key=key, attr=body[1:], expect=self.template(rule["expect"], f"{rule_pointer}/expect", variables, path)))
        if descriptor is not None:
            for attr, _ in predicates:
                self.check_attribute(descriptor, attr, f"{pointer}/with", path)
            for correlation in correlations:
                self.check_attribute(descriptor, correlation.attr, f"{pointer}/correlate/{escape_pointer_token(correlation.key)}/from", path)
        return EventFilterSpec(event_type=event_type, predicates=tuple(predicates), correlations=tuple(correlations), pointer=pointer)

    def resolve_event(self, event_type: str, pointer: str, path: str) -> EventTypeDescriptor | None:
        descriptor = self.events.get(event_type) or self.c.event_catalog.resolve(event_type)
        if descriptor is None:
            self.error("event.unknown_type", f"event type '{event_type}' is not in the event catalog", pointer, path)
            return None
        self.events[event_type] = descriptor
        return descriptor

    def check_attribute(self, descriptor: EventTypeDescriptor, attr: str, pointer: str, path: str) -> None:
        head, *rest = attr.split(".")
        if head in ENVELOPE_ATTRS and not rest:
            return
        if head == "data":
            schema = descriptor.data_schema
        elif head == "attributes" and rest:
            schema = descriptor.attributes_schema
        else:
            self.error("event.unknown_attribute", f"'{attr}' is not an attribute of events", pointer, path)
            return
        if schema is not None and not _schema_has_path(schema, rest):
            self.error("event.unknown_attribute", f"'{attr}' is not declared in the schema of '{descriptor.type}'", pointer, path)

    def task_emit(self, body, pointer, path, depth, variables, spec):
        emit = body["emit"]
        emit_pointer = f"{pointer}/emit"
        if not self.is_map(emit, emit_pointer, "emit", path):
            return
        self.only_keys(emit, {"event"}, emit_pointer, "emit", path)
        event = emit.get("event")
        if not self.is_map(event, f"{emit_pointer}/event", "emit.event", path):
            return
        self.only_keys(event, {"with"}, f"{emit_pointer}/event", "emit.event", path)
        properties = event.get("with")
        properties_pointer = f"{emit_pointer}/event/with"
        if not self.is_map(properties, properties_pointer, "emit.event.with", path):
            return
        event_type = properties.get("type")
        if not isinstance(event_type, str) or is_expression(event_type):
            self.error("emit.type", "emit.event.with.type must be a literal event type", properties_pointer, path)
            return
        descriptor = self.resolve_event(event_type, f"{properties_pointer}/type", path)
        template = self.template(properties, properties_pointer, variables, path)
        if descriptor is not None and descriptor.data_schema is not None and "data" in properties and not is_expression(properties["data"]):
            data_template = self.template(properties["data"], f"{properties_pointer}/data", variables, path)
            if data_template.is_const:
                for problem in self.c.schemas.validate(descriptor.data_schema, data_template.value):
                    self.error("emit.data_schema", f"event data does not match the schema of '{event_type}': {problem}", f"{properties_pointer}/data", path)
        spec.update(template=template, emit_type=descriptor)

    def task_fork(self, body, pointer, path, depth, variables, spec):
        fork = body["fork"]
        fork_pointer = f"{pointer}/fork"
        if not self.is_map(fork, fork_pointer, "fork", path):
            return
        self.only_keys(fork, {"branches", "compete"}, fork_pointer, "fork", path)
        compete = fork.get("compete", False)
        if not isinstance(compete, bool):
            self.error("fork.compete", "fork.compete must be a boolean", f"{fork_pointer}/compete", path)
            compete = False
        branches = fork.get("branches")
        if isinstance(branches, list) and not branches:
            self.error("fork.empty", "fork.branches needs at least one branch", f"{fork_pointer}/branches", path)
        spec.update(
            compete=compete,
            children={"branches": self.task_list(branches, f"{fork_pointer}/branches", path, "branches", depth + 1, variables)},
            compat_key=f"fork:{compete}",
        )


def _flatten(prefix: str, value: dict, pointer: str):
    for key, item in value.items():
        attr = f"{prefix}.{key}"
        item_pointer = f"{pointer}/{escape_pointer_token(key)}"
        if isinstance(item, dict) and item:
            yield from _flatten(attr, item, item_pointer)
        else:
            yield attr, item, item_pointer


def _schema_has_path(schema: dict, segments: list[str]) -> bool:
    current = schema
    for segment in segments:
        if not isinstance(current, dict):
            return True
        properties = current.get("properties")
        if properties is None:
            return True
        if segment in properties:
            current = properties[segment]
            continue
        return current.get("additionalProperties") is True
    return True
