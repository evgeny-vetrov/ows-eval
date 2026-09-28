class ManagerCrash(Exception):
    pass


class CrashingRuntime:
    """Wraps a runtime: the n-th mutating call (`control`, `swap_version`) crashes the
    manager right before or right after it reaches the runtime."""

    def __init__(self, inner, crash_at: int, after: bool):
        self.inner = inner
        self.crash_at = crash_at
        self.after = after
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def _mutate(self, name: str, *args, **kwargs) -> None:
        self.calls += 1
        crash = self.calls == self.crash_at
        if crash and not self.after:
            raise ManagerCrash(f"before {name} #{self.calls}")
        await getattr(self.inner, name)(*args, **kwargs)
        if crash:
            raise ManagerCrash(f"after {name} #{self.calls}")

    async def control(self, *args, **kwargs) -> None:
        await self._mutate("control", *args, **kwargs)

    async def swap_version(self, *args, **kwargs) -> None:
        await self._mutate("swap_version", *args, **kwargs)
