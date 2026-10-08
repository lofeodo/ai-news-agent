REPORT_DUPLICATES_TOOL = {
    "name": "report_duplicates",
    "description": (
        "Report which articles cover the same underlying story. "
        "Call this exactly once per turn; use an empty list when there are no duplicates."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "groups": {
                "type": "array",
                "description": "One entry per set of articles that cover the same story. Empty if none.",
                "items": {
                    "type": "object",
                    "properties": {
                        "indices": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "Article numbers (the N in [N]) that cover the same story; at least 2"
                        },
                        "reason": {
                            "type": "string",
                            "description": "One short sentence naming the shared event"
                        }
                    },
                    "required": ["indices", "reason"]
                }
            }
        },
        "required": ["groups"]
    }
}
