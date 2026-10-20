from datetime import date

import pytest

from app.db import Database, Delivery, UserError


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


def test_first_admin_and_login(db):
    assert not db.has_users()
    admin = db.add_user("admin", "Мария", "secret", is_admin=True)
    assert db.has_users()
    assert db.authenticate("admin", "secret") == admin
    assert db.authenticate("ADMIN", "secret") == admin  # логин без учёта регистра
    assert db.authenticate("admin", "wrong") is None
    assert db.authenticate("nobody", "secret") is None


def test_duplicate_login_and_empty_password(db):
    db.add_user("anna", "", "1")
    with pytest.raises(UserError):
        db.add_user("Anna", "", "2")
    with pytest.raises(UserError):
        db.add_user("oleg", "", "")


def test_change_password(db):
    u = db.add_user("anna", "Анна", "old")
    db.set_password(u.id, "new")
    assert db.authenticate("anna", "old") is None
    assert db.authenticate("anna", "new") is not None


def test_last_admin_is_protected(db):
    admin = db.add_user("admin", "", "1", is_admin=True)
    with pytest.raises(UserError):
        db.update_user(admin.id, "", is_admin=False)
    with pytest.raises(UserError):
        db.delete_user(admin.id)
    second = db.add_user("boss2", "", "1", is_admin=True)
    db.update_user(admin.id, "", is_admin=False)
    assert [u.is_admin for u in db.users() if u.id == second.id] == [True]


def test_authorship(db):
    anna = db.add_user("anna", "Анна", "1")
    oleg = db.add_user("oleg", "", "1")
    d = Delivery(day=date(2026, 10, 20), items="Розы", created_by=anna.id)
    db.add(d)
    db.set_delivered(d.id, True, oleg.id)
    stored = db.for_day(d.day)[0]
    assert (stored.created_by_name, stored.delivered_by_name) == ("Анна", "oleg")

    db.set_delivered(d.id, False, oleg.id)
    assert db.for_day(d.day)[0].delivered_by is None

    db.delete_user(anna.id)  # доставка остаётся, автор пропадает
    stored = db.for_day(d.day)[0]
    assert stored.items == "Розы" and stored.created_by_name == ""


def test_old_database_is_migrated(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE deliveries (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL,"
                 " time TEXT NOT NULL DEFAULT '', items TEXT NOT NULL DEFAULT '',"
                 " recipient TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',"
                 " address TEXT NOT NULL DEFAULT '', price INTEGER NOT NULL DEFAULT 0,"
                 " courier_pay INTEGER NOT NULL DEFAULT 0, delivered INTEGER NOT NULL DEFAULT 0)")
    conn.execute("INSERT INTO deliveries (day, items) VALUES ('2026-10-20', 'Старая')")
    conn.commit()
    conn.close()

    db = Database(str(path))
    [d] = db.for_day(date(2026, 10, 20))
    assert d.items == "Старая" and d.created_by is None
    db.close()
