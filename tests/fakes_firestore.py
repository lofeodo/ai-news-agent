"""Minimal Firestore fake for tests: collection(name).document(id).get()/.set()/.update() and
collection(name).where(...).order_by(...).limit(n).stream() (equality and array_contains filters)."""


class _Snap:
    def __init__(self, id, data, reference=None):
        self.id, self._data = id, data
        self.exists = data is not None
        self.reference = reference

    def to_dict(self):
        return None if self._data is None else dict(self._data)


class _Doc:
    def __init__(self, store, name, id):
        self._store, self._name, self._id = store, name, id

    def get(self, transaction=None):
        return _Snap(self._id, self._store.get(self._name, {}).get(self._id), self)

    def set(self, data, merge=False):
        coll = self._store.setdefault(self._name, {})
        if merge and self._id in coll:
            return self.update(data)
        coll[self._id] = dict(data)

    def update(self, data):
        from google.cloud import firestore
        coll = self._store.setdefault(self._name, {})
        doc = coll.setdefault(self._id, {})
        for k, v in data.items():
            if v is firestore.DELETE_FIELD:
                doc.pop(k, None)
            else:
                doc[k] = v

    def collection(self, name):
        """A subcollection, stored flat under the key 'parent/id/name'."""
        return _make_collection(self._store, f"{self._name}/{self._id}/{name}")


def _matches(doc, field, op, value):
    if op == "array_contains":
        return value in (doc.get(field) or [])
    return doc.get(field) == value


class _Query:
    def __init__(self, store, name, limit=None, filters=()):
        self._store, self._name, self._limit, self._filters = store, name, limit, tuple(filters)

    def order_by(self, field, direction=None):
        self._field = field
        return self

    def where(self, field, op, value):
        assert op in ("==", "array_contains"), "the fake only supports equality and array_contains filters"
        return _Query(self._store, self._name, self._limit, self._filters + ((field, op, value),))

    def select(self, fields):
        return self  # the fake always returns whole docs

    def limit(self, n):
        return _Query(self._store, self._name, n, self._filters)

    def stream(self):
        # Newest first by started_at (ISO strings sort lexicographically).
        rows = sorted(((i, d) for i, d in self._store.get(self._name, {}).items()
                       if all(_matches(d, *f) for f in self._filters)),
                      key=lambda kv: str(kv[1].get("started_at", "")), reverse=True)
        rows = rows[: self._limit] if self._limit else rows
        return [_Snap(i, d, _Doc(self._store, self._name, i)) for i, d in rows]


def _make_collection(store, name):
    class _Coll(_Query):
        def document(self, id):
            return _Doc(store, name, id)

    return _Coll(store, name)


class FakeDb:
    def __init__(self, store=None):
        self.store = store if store is not None else {}

    def collection(self, name):
        return _make_collection(self.store, name)
