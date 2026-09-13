# NADZOR.AI automation invariants

Ground truth stays outside runtime. Blind runs use fresh LLM inference. GigaChat review runs after each evaluation. Runtime logs stay local; only sanitized handoff summaries may be published. Never modify main from the automation loop.
