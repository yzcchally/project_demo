from domain import WorkflowError, make_draft


class Contacts:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS contacts(alias TEXT PRIMARY KEY, address TEXT NOT NULL)")

    def all(self):
        with self.store.connect() as db:
            return [{"alias": a, "address": e} for a, e in db.execute("SELECT alias,address FROM contacts ORDER BY alias")]

    def put(self, alias, address):
        alias = alias.strip()
        if not alias or len(alias) > 80:
            raise WorkflowError("请填写一个明确的联系人称呼")
        address = make_draft(address, "联系人核对", "test")["recipient"]
        with self.store.connect() as db:
            db.execute("INSERT INTO contacts VALUES(?,?) ON CONFLICT(alias) DO UPDATE SET address=excluded.address", (alias, address))
        return {"alias": alias, "address": address}

    def delete(self, alias):
        with self.store.connect() as db:
            db.execute("DELETE FROM contacts WHERE alias=?", (alias,))

    def resolve(self, alias):
        return next((c["address"] for c in self.all() if c["alias"] == alias.strip()), "")
