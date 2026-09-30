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

# ---------------------------------------------------------------------------
# LangGraph variant (agent1b graph mode)
# ---------------------------------------------------------------------------
import copy

CATEGORIES = FILTER_TOOL["input_schema"]["properties"]["articles"]["items"]["properties"]["category"]["enum"]

# Same tool name/shape as FILTER_TOOL plus a per-article confidence. FILTER_TOOL
# itself is left untouched so the single-pass path behaves exactly as before.
FILTER_TOOL_WITH_CONFIDENCE = copy.deepcopy(FILTER_TOOL)
_item = FILTER_TOOL_WITH_CONFIDENCE["input_schema"]["properties"]["articles"]["items"]
_item["properties"]["confidence"] = {
    "type": "integer",
    "enum": [1, 2, 3, 4, 5],
    "description": "How confident you are that the assigned category is the right one (5 = certain, 1 = guess)"
}
_item["required"] = ["index", "category", "confidence"]

FETCH_ARTICLE_TOOL = {
    "name": "fetch_article_text",
    "description": "Download the full text of the article at this URL (first ~1500 words). Use it when the title and description are not enough to pick the category.",
    "input_schema": {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "The article URL"}},
        "required": ["url"]
    }
}

SUBMIT_CATEGORY_TOOL = {
    "name": "submit_category",
    "description": "Submit the final category for the article. Call this exactly once when you are done.",
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": CATEGORIES},
            "reason": {"type": "string", "description": "One short sentence"}
        },
        "required": ["category", "reason"]
    }
}
