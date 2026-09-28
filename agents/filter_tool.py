LANGUAGE_FILTER_TOOL = {
    "name": "filter_by_language",
    "description": "Classify the language of every article as English, French, or other",
    "input_schema": {
        "type": "object",
        "properties": {
            "articles": {
                "type": "array",
                "description": "One entry for every input article, in input order",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "0-based index of the article from the input list"
                        },
                        "language": {
                            "type": "string",
                            "enum": ["en", "fr", "other"],
                            "description": "Language the article is written in; \"other\" for any language that is not English or French"
                        }
                    },
                    "required": ["index", "language"]
                }
            }
        },
        "required": ["articles"]
    }
}

FILTER_TOOL = {
    "name": "filter_articles",
    "description": "Filter a list of news articles for AI relevance and assign each a category",
    "input_schema": {
        "type": "object",
        "properties": {
            "articles": {
                "type": "array",
                "description": "List of selected articles with their index and assigned category",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "0-based index of the article from the input list"
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "Model & Product Releases",
                                "Industry & Business",
                                "Policy, Law & Regulation",
                                "Open Source & Tools",
                                "Safety & Alignment",
                                "Society & Culture",
                                "Canada & Montreal"
                            ],
                            "description": "The most appropriate category for this article"
                        }
                    },
                    "required": ["index", "category"]
                }
            }
        },
        "required": ["articles"]
    }
}