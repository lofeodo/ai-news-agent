SCORING_TOOL = {
    "name": "score_paper",
    "description": "Score a research paper on 8 dimensions",
    "input_schema": {
        "type": "object",
        "properties": {
            "novelty":                 {"type": "integer", "minimum": 1, "maximum": 5},
            "rigor":                   {"type": "integer", "minimum": 1, "maximum": 3},
            "reproducibility":         {"type": "integer", "minimum": 1, "maximum": 3},
            "clarity":                 {"type": "integer", "minimum": 1, "maximum": 3},
            "practical_applicability": {"type": "integer", "minimum": 1, "maximum": 5},
            "significance":            {"type": "integer", "minimum": 1, "maximum": 4},
            "disruption_potential":    {"type": "integer", "minimum": 1, "maximum": 5},
            "wow_factor":              {"type": "integer", "minimum": 1, "maximum": 5},
            "total":                   {"type": "integer", "minimum": 8, "maximum": 33},
            "reasoning":               {"type": "string"}
        },
        "required": [
            "novelty", "rigor", "reproducibility", "clarity",
            "practical_applicability", "significance",
            "disruption_potential", "wow_factor", "total", "reasoning"
        ]
    }
}