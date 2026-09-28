"""Persisted state written by engine 0.1.0 (state schema 1), frozen on purpose.

A pilot fabula waiting for the "PR merged" event. Newer engines must keep loading it:
change the layout only together with a migration in `core/codec.py`. Do not regenerate.
"""

STATE_V1 = r"""
{
 "schema_version": 1,
 "engine_version": "0.1.0",
 "semantics": {
  "engine_version": "0.1.0",
  "flags": {
   "listen_all_order": "declaration"
  }
 },
 "fabula_id": "f-1",
 "status": "waiting",
 "scenario": {
  "schema_version": 1,
  "ref": "gena/ticket-to-prod:1.0.0",
  "digest": "sha256:800cc9d4ac4cc2a1d5af70aaa6eeff4345e99f490785e8728aff91651446fffa",
  "document": {
   "document": {
    "dsl": "1.0.3",
    "namespace": "gena",
    "name": "ticket-to-prod",
    "version": "1.0.0",
    "title": "From a ticket to a deployed change"
   },
   "input": {
    "schema": {
     "document": {
      "type": "object",
      "required": [
       "ticket"
      ],
      "properties": {
       "ticket": {
        "type": "string"
       }
      }
     }
    }
   },
   "timeout": {
    "after": "P60D"
   },
   "do": [
    {
     "runAgent": {
      "call": "agent.run:1@gena",
      "with": {
       "ticket": "${ .ticket }"
      },
      "timeout": {
       "after": "P2D"
      },
      "export": {
       "as": "${ {ticket: $input.ticket, pr: .pr_id} }"
      }
     }
    },
    {
     "awaitMerge": {
      "listen": {
       "to": {
        "one": {
         "with": {
          "type": "vcs.pr.merged"
         },
         "correlate": {
          "pr": {
           "from": "${ .data.pr_id }",
           "expect": "${ $context.pr }"
          }
         }
        }
       }
      },
      "timeout": {
       "after": "P30D"
      }
     }
    },
    {
     "soak": {
      "wait": "P3D"
     }
    },
    {
     "healthCheck": {
      "try": [
       {
        "probe": {
         "call": "http.healthcheck:1@platform",
         "with": {
          "url": "https://service.example/health"
         }
        }
       }
      ],
      "catch": {
       "errors": {
        "with": {
         "type": "https://serverlessworkflow.io/spec/1.0.0/errors/communication"
        }
       },
       "retry": {
        "delay": "PT1M",
        "backoff": {
         "exponential": {}
        },
        "limit": {
         "attempt": {
          "count": 3
         }
        }
       }
      }
     }
    },
    {
     "approve": {
      "call": "human.approve:1@gena",
      "with": {
       "ticket": "${ $context.ticket }",
       "question": "${ \"Ship \" + $context.pr + \"?\" }"
      },
      "export": {
       "as": "${ $context + {approved: .approved} }"
      }
     }
    },
    {
     "decide": {
      "switch": [
       {
        "approved": {
         "when": "${ $context.approved }",
         "then": "comment"
        }
       },
       {
        "rejected": {
         "then": "end"
        }
       }
      ]
     }
    },
    {
     "comment": {
      "call": "tracker.comment:1@tracker",
      "with": {
       "ticket": "${ $context.ticket }",
       "text": "${ \"Deployed \" + $context.pr + \" after a health check\" }"
      }
     }
    }
   ],
   "output": {
    "as": "${ {ticket: $context.ticket, pr: $context.pr, approved: $context.approved} }"
   }
  },
  "capabilities": {
   "agent.run:1@gena": {
    "ref": "agent.run:1@gena",
    "title": "Run an agent on a ticket",
    "input_schema": {
     "type": "object",
     "required": [
      "ticket"
     ],
     "properties": {
      "ticket": {
       "type": "string"
      }
     }
    },
    "output_schema": {
     "type": "object",
     "required": [
      "pr_id"
     ],
     "properties": {
      "pr_id": {
       "type": "string"
      }
     }
    },
    "effects": [
     "writes:vcs"
    ],
    "default_timeout": "P1D",
    "default_retry": null,
    "cancellable": true
   },
   "http.healthcheck:1@platform": {
    "ref": "http.healthcheck:1@platform",
    "title": null,
    "input_schema": {
     "type": "object",
     "required": [
      "url"
     ]
    },
    "output_schema": {
     "type": "object",
     "required": [
      "status"
     ]
    },
    "effects": [
     "reads:network"
    ],
    "default_timeout": "PT5M",
    "default_retry": {
     "max_retries": 1,
     "delay": "PT5S",
     "backoff": "constant",
     "jitter": "PT0S",
     "retry_on": [
      "https://serverlessworkflow.io/spec/1.0.0/errors/communication"
     ]
    },
    "cancellable": true
   },
   "human.approve:1@gena": {
    "ref": "human.approve:1@gena",
    "title": null,
    "input_schema": {
     "type": "object",
     "required": [
      "ticket",
      "question"
     ]
    },
    "output_schema": {
     "type": "object",
     "required": [
      "approved"
     ],
     "properties": {
      "approved": {
       "type": "boolean"
      }
     }
    },
    "effects": [
     "asks:human"
    ],
    "default_timeout": null,
    "default_retry": null,
    "cancellable": true
   },
   "tracker.comment:1@tracker": {
    "ref": "tracker.comment:1@tracker",
    "title": null,
    "input_schema": {
     "type": "object",
     "required": [
      "ticket",
      "text"
     ]
    },
    "output_schema": null,
    "effects": [
     "writes:tracker"
    ],
    "default_timeout": null,
    "default_retry": null,
    "cancellable": true
   }
  },
  "events": {
   "vcs.pr.merged": {
    "type": "vcs.pr.merged",
    "data_schema": {
     "type": "object",
     "properties": {
      "pr_id": {
       "type": "string"
      },
      "repo": {
       "type": "string"
      }
     }
    },
    "attributes_schema": null
   }
  }
 },
 "execution_context": {
  "run_as": "tester",
  "trigger": null,
  "labels": {}
 },
 "workflow_input": {
  "ticket": "T-42"
 },
 "data": {
  "ticket": "T-42",
  "pr": "PR-T-42"
 },
 "started_at": "2026-01-01T00:00:00Z",
 "last_at": "2026-01-01T00:00:00Z",
 "frames": {
  "/": {
   "path": "/",
   "visit": 1,
   "attempt": 1,
   "raw_input": {
    "ticket": "T-42"
   },
   "input": {
    "ticket": "T-42"
   },
   "started_at": "2026-01-01T00:00:00Z",
   "timeout_op": "f-1/@workflow/timeout",
   "failed": null,
   "kind": "workflow",
   "current": "/awaitMerge"
  },
  "/awaitMerge": {
   "path": "/awaitMerge",
   "visit": 1,
   "attempt": 1,
   "raw_input": {
    "pr_id": "PR-T-42"
   },
   "input": {
    "pr_id": "PR-T-42"
   },
   "started_at": "2026-01-01T00:00:00Z",
   "timeout_op": "f-1/awaitMerge@1.1/timeout",
   "failed": null,
   "kind": "listen",
   "since": "2026-01-01T00:00:00Z",
   "main_subs": [
    "f-1/awaitMerge@1.1/sub.0"
   ],
   "until_subs": [],
   "until_hits": [],
   "events": [],
   "by_filter": [
    null
   ],
   "satisfied": [
    false
   ],
   "buffer": [],
   "processing": false,
   "closing": false,
   "item": null,
   "item_index": 0,
   "acc": {
    "pr_id": "PR-T-42"
   },
   "current": null
  }
 },
 "inflight": {
  "f-1/@workflow/timeout": {
   "op_id": "f-1/@workflow/timeout",
   "op": "timer",
   "role": "workflow_timeout",
   "owner": "/",
   "fire_at": "2026-03-02T00:00:00Z",
   "capability_ref": null,
   "cancellable": true,
   "index": null,
   "subscription": null,
   "delivery_keys": []
  },
  "f-1/awaitMerge@1.1/timeout": {
   "op_id": "f-1/awaitMerge@1.1/timeout",
   "op": "timer",
   "role": "timeout",
   "owner": "/awaitMerge",
   "fire_at": "2026-01-31T00:00:00Z",
   "capability_ref": null,
   "cancellable": true,
   "index": null,
   "subscription": null,
   "delivery_keys": []
  },
  "f-1/awaitMerge@1.1/sub.0": {
   "op_id": "f-1/awaitMerge@1.1/sub.0",
   "op": "subscription",
   "role": "sub",
   "owner": "/awaitMerge",
   "fire_at": null,
   "capability_ref": null,
   "cancellable": true,
   "index": 0,
   "subscription": {
    "kind": "subscribe",
    "subscription_id": "f-1/awaitMerge@1.1/sub.0",
    "event_types": [
     "vcs.pr.merged"
    ],
    "filter": {
     "kind": "predicate",
     "attr": "data.pr_id",
     "op": "eq",
     "value": "PR-T-42"
    },
    "since": "2026-01-01T00:00:00Z",
    "expires_at": "2026-02-01T00:00:00Z",
    "context": {
     "run_as": "tester",
     "trigger": null,
     "labels": {}
    }
   },
   "delivery_keys": []
  }
 },
 "visits": {
  "/": 1,
  "/runAgent": 1,
  "/awaitMerge": 1
 },
 "agenda": [],
 "paused_queue": [],
 "recent_stimuli": [
  "s1"
 ],
 "recent_controls": [],
 "yield_seq": 0,
 "output": null,
 "problem": null,
 "failed_node": null,
 "last_error": null
}
"""
