"""Scripted stand-ins for the Anthropic client and the article fetcher. No network."""
import re
from types import SimpleNamespace

CATS = [
    "Model & Product Releases", "Industry & Business", "Policy, Law & Regulation",
    "Open Source & Tools", "Safety & Alignment", "Society & Culture", "Canada & Montreal",
]


def tool_use(name, **inp):
    return SimpleNamespace(type="tool_use", id=f"tu_{name}", name=name, input=inp)


def response(*blocks):
    return SimpleNamespace(
        content=list(blocks), stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


class FakeClient:
    """confidences: {title: int|None}. review: 'submit' | 'fetch_then_submit' | 'always_fetch' | 'raise'."""

    def __init__(self, confidences, review="submit", first_category=CATS[1], review_category=CATS[5]):
        self.messages = self
        self.confidences = confidences
        self.review = review
        self.first_category = first_category
        self.review_category = review_category
        self.calls = []          # (kind, kwargs)

    def create(self, **kw):
        names = [t["name"] for t in kw["tools"]]
        prompt = kw["messages"][0]["content"]
        if "filter_by_language" in names:
            self.calls.append(("language", kw))
            idx = sorted({int(i) for i in re.findall(r"<article_(\d+)>", prompt)})
            return response(tool_use("filter_by_language",
                                     articles=[{"index": i, "language": "en"} for i in idx]))
        if "filter_articles" in names:
            self.calls.append(("categorize", kw))
            props = kw["tools"][0]["input_schema"]["properties"]["articles"]["items"]["properties"]
            items = []
            for i, title in re.findall(r"\[(\d+)\] (?:\[HN: \d+ points\] )?(T\d+)", prompt):
                item = {"index": int(i), "category": self.first_category}
                if "confidence" in props:
                    item["confidence"] = self.confidences[title]
                items.append(item)
            return response(tool_use("filter_articles", articles=items))
        # review loop
        self.calls.append(("review", kw))
        turn = len(kw["messages"])
        if self.review == "raise":
            raise RuntimeError("llm down")
        if self.review == "always_fetch":
            return response(tool_use("fetch_article_text", url="https://example.com/x"))
        if self.review == "fetch_then_submit" and turn == 1:
            return response(tool_use("fetch_article_text", url="https://example.com/x"))
        return response(tool_use("submit_category", category=self.review_category, reason="ok"))

    def n(self, kind):
        return sum(1 for k, _ in self.calls if k == kind)


class Fetcher:
    def __init__(self, result=("some article text " * 60, None), raises=None):
        self.result, self.raises, self.urls = result, raises, []

    def __call__(self, url, timeout):
        self.urls.append(url)
        if self.raises:
            raise self.raises
        return self.result


def make_articles(n):
    return [{"source": "hackernews", "title": f"T{i}", "description": "", "url": f"https://example.com/{i}",
             "language": "en", "hn_score": 0} for i in range(n)]
