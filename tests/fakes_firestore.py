"""Minimal Firestore fake for tests: collection(name).document(id).get()/.set() and
collection(name).order_by(...).limit(n).stream()."""


class _Snap:
    def __init__(self, id, data):
        self.id, self._data = id, data
        self.exists = data is not None

    def to_dict(self):
        return None if self._data is None else dict(self._data)


class _Doc:
    def __init__(self, store, name, id):
        self._store, self._name, self._id = store, name, id

    def get(self):
        return _Snap(self._id, self._store.get(self._name, {}).get(self._id))

    def set(self, data, merge=False):
        coll = self._store.setdefault(self._name, {})
        coll[self._id] = {**coll.get(self._id, {}), **data} if merge else dict(data)


class _Query:
    def __init__(self, store, name, limit=None):
        self._store, self._name, self._limit = store, name, limit

    def order_by(self, field, direction=None):
        self._field = field
        return self

    def limit(self, n):
        return _Query(self._store, self._name, n)

    def stream(self):
        # Newest first by started_at (ISO strings sort lexicographically).
        rows = sorted(self._store.get(self._name, {}).items(),
                      key=lambda kv: kv[1].get("started_at", ""), reverse=True)
        rows = rows[: self._limit] if self._limit else rows
        return [_Snap(i, d) for i, d in rows]


class FakeDb:
    def __init__(self, store=None):
        self.store = store if store is not None else {}

    def collection(self, name):
        db = self

        class _Coll(_Query):
            def document(self, id):
                return _Doc(db.store, name, id)

        return _Coll(self.store, name)
