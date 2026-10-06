"""Source persistence for the judge: stub Firestore, no network."""
import summary_sources as ss
import agent2b_summarize_news as a2b


class Batch:
    def __init__(self, db):
        self.db, self.ops = db, []

    def set(self, ref, data):
        self.ops.append((ref, data))

    def commit(self):
        for ref, data in self.ops:
            ref.set(data)


class Db:
    def __init__(self):
        from fakes_firestore import FakeDb
        self._f = FakeDb()
        self.store = self._f.store

    def collection(self, n):
        return self._f.collection(n)

    def batch(self):
        return Batch(self)


def test_roundtrip_and_ttl():
    db = Db()
    n = ss.save_sources(db, "r1", "news", [{"ident": "http://a", "title": "A", "text": "body", "used_fallback": False},
                                           {"ident": "http://b", "title": "B", "text": "", "used_fallback": True}])
    assert n == 1  # empty text skipped
    got = ss.load_source(db, "r1", "news", "http://a")
    assert got["text"] == "body" and got["expires_at"] is not None and got["used_fallback"] is False
    assert ss.load_source(db, "r1", "news", "http://b") is None
    assert ss.load_source(db, "r2", "news", "http://a") is None


def test_write_failure_never_raises():
    class Boom:
        def batch(self):
            raise RuntimeError("firestore down")
    assert ss.save_sources(Boom(), "r", "news", [{"ident": "x", "text": "t"}]) == 0


def test_summarize_article_returns_source_for_run_to_pop():
    from types import SimpleNamespace

    class C:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kw):
            return SimpleNamespace(content=[SimpleNamespace(text="A summary.")])

    r = a2b.summarize_article({"title": "T", "url": "u", "description": "d"}, "full text", "{title}{text}{style_instruction}",
                              "{title}{description}{style_instruction}", "", C())
    assert r["_source"] == "full text"
    r = a2b.summarize_article({"title": "T", "url": "u", "description": "desc"}, None, "x", "{title}{description}{style_instruction}", "", C())
    assert r["_source"] == "desc" and r["used_fallback"] is True
