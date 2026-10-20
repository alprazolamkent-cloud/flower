"""Вход, создание сотрудников, смена пароля и список сотрудников."""

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .db import Database, User, UserError
from .icon import flower_icon
from .theme import C


def _password_edit() -> QLineEdit:
    edit = QLineEdit()
    edit.setEchoMode(QLineEdit.Password)
    return edit


def _buttons(dialog: QDialog, ok_text: str) -> QDialogButtonBox:
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Ok).setText(ok_text)
    buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return buttons


class LoginDialog(QDialog):
    def __init__(self, parent, db: Database, note: str = ""):
        super().__init__(parent)
        self.db = db
        self.user: User | None = None
        self.settings = QSettings("FlowerDeliveries", "FlowerDeliveries")
        self.setWindowTitle("Вход — AloFlorist")
        self.setWindowIcon(flower_icon())
        self.setMinimumWidth(340)

        form = QFormLayout(self)
        title = QLabel("<h3>AloFlorist</h3>Войдите под своим логином")
        form.addRow(title)
        if note:
            note_label = QLabel(note)
            note_label.setWordWrap(True)
            note_label.setStyleSheet(f"background: {C.info_bg}; border: 1px solid {C.info_border}; "
                                     f"color: {C.note_fg}; padding: 6px")
            form.addRow(note_label)
        self.login_edit = QLineEdit(self.settings.value("last_login", ""))
        form.addRow("Логин:", self.login_edit)
        self.password_edit = _password_edit()
        form.addRow("Пароль:", self.password_edit)
        form.addRow(_buttons(self, "Войти"))
        (self.password_edit if self.login_edit.text() else self.login_edit).setFocus()

    def accept(self):
        user = self.db.authenticate(self.login_edit.text(), self.password_edit.text())
        if user is None:
            self.db.log_action(None, "Неудачный вход", f"логин: {self.login_edit.text().strip() or '—'}",
                               who=self.login_edit.text().strip() or "—")
            QMessageBox.warning(self, "Не удалось войти", "Неверный логин или пароль.")
            self.password_edit.clear()
            self.password_edit.setFocus()
            return
        self.settings.setValue("last_login", user.login)
        self.user = user
        super().accept()


class UserFormDialog(QDialog):
    """Новый сотрудник (или первый администратор)."""

    def __init__(self, parent, db: Database, first_admin: bool = False):
        super().__init__(parent)
        self.db = db
        self.first_admin = first_admin
        self.user: User | None = None
        self.setWindowTitle("Создание администратора" if first_admin else "Новый сотрудник")
        self.setWindowIcon(flower_icon())
        self.setMinimumWidth(380)

        form = QFormLayout(self)
        if first_admin:
            intro = QLabel("Это первый запуск. Создайте главный профиль администратора — "
                           "он сможет добавлять сотрудников. Запомните пароль!")
            intro.setWordWrap(True)
            form.addRow(intro)
        self.login_edit = QLineEdit()
        form.addRow("Логин:", self.login_edit)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("например Анна")
        form.addRow("Имя:", self.name_edit)
        self.password_edit = _password_edit()
        form.addRow("Пароль:", self.password_edit)
        self.password2_edit = _password_edit()
        form.addRow("Ещё раз пароль:", self.password2_edit)
        self.admin_check = QCheckBox("Администратор (может управлять сотрудниками)")
        self.admin_check.setVisible(not first_admin)
        form.addRow(self.admin_check)
        form.addRow(_buttons(self, "Создать"))

    def accept(self):
        if self.password_edit.text() != self.password2_edit.text():
            QMessageBox.warning(self, "Пароли не совпадают", "Введите пароль одинаково два раза.")
            return
        try:
            self.user = self.db.add_user(
                self.login_edit.text(), self.name_edit.text(), self.password_edit.text(),
                is_admin=self.first_admin or self.admin_check.isChecked(),
            )
        except UserError as e:
            QMessageBox.warning(self, "Ошибка", str(e))
            return
        super().accept()


class PasswordDialog(QDialog):
    """Смена пароля. Свой пароль — со вводом старого; админ сбрасывает чужой без него."""

    def __init__(self, parent, db: Database, user: User, need_old: bool, actor: User | None = None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.actor = actor or user  # кто меняет: сам сотрудник или администратор
        self.need_old = need_old
        self.setWindowTitle(f"Пароль: {user.name or user.login}")
        self.setMinimumWidth(340)

        form = QFormLayout(self)
        self.old_edit = _password_edit()
        if need_old:
            form.addRow("Старый пароль:", self.old_edit)
        self.new_edit = _password_edit()
        form.addRow("Новый пароль:", self.new_edit)
        self.new2_edit = _password_edit()
        form.addRow("Ещё раз:", self.new2_edit)
        form.addRow(_buttons(self, "Сохранить"))

    def accept(self):
        if self.need_old and self.db.authenticate(self.user.login, self.old_edit.text()) is None:
            QMessageBox.warning(self, "Ошибка", "Старый пароль введён неверно.")
            return
        if self.new_edit.text() != self.new2_edit.text():
            QMessageBox.warning(self, "Пароли не совпадают", "Введите новый пароль одинаково два раза.")
            return
        try:
            self.db.set_password(self.user.id, self.new_edit.text())
        except UserError as e:
            QMessageBox.warning(self, "Ошибка", str(e))
            return
        who = self.user.name or self.user.login
        self.db.log_action(self.actor, "Пароль изменён" if self.actor.id == self.user.id else "Пароль сброшен",
                           f"сотрудник: {who}")  # сам пароль, конечно, не записывается
        super().accept()


class UsersDialog(QDialog):
    """Список сотрудников — только для администратора."""

    def __init__(self, parent, db: Database, current: User):
        super().__init__(parent)
        self.db = db
        self.current = current
        self.users: list[User] = []
        self.setWindowTitle("Сотрудники")
        self.resize(560, 380)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Логин", "Имя", "Роль"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        for text, slot in (("➕ Добавить", self.add_user),
                           ("🔑 Сменить пароль", self.reset_password),
                           ("👑 Админ да/нет", self.toggle_admin),
                           ("🗑 Удалить", self.delete_user)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        layout.addLayout(buttons)

        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).setText("Закрыть")
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.refresh()

    def refresh(self):
        self.users = self.db.users()
        self.table.setRowCount(len(self.users))
        for row, u in enumerate(self.users):
            role = "Администратор" if u.is_admin else "Сотрудник"
            if u.id == self.current.id:
                role += " (вы)"
            for col, text in enumerate((u.login, u.name, role)):
                self.table.setItem(row, col, QTableWidgetItem(text))

    def selected(self) -> User | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.users) and self.table.selectionModel().hasSelection():
            return self.users[row]
        QMessageBox.information(self, "Сотрудники", "Сначала выберите сотрудника в списке.")
        return None

    def add_user(self):
        dlg = UserFormDialog(self, self.db)
        if dlg.exec() == QDialog.Accepted:
            u = dlg.user
            self.db.log_action(self.current, "Сотрудник создан",
                               f"{u.name or u.login} (логин {u.login}){', администратор' if u.is_admin else ''}")
            self.refresh()

    def reset_password(self):
        u = self.selected()
        if u:
            dlg = PasswordDialog(self, self.db, u, need_old=False, actor=self.current)
            if dlg.exec() == QDialog.Accepted:
                QMessageBox.information(self, "Готово", f"Пароль для «{u.login}» изменён.")

    def toggle_admin(self):
        u = self.selected()
        if not u:
            return
        try:
            self.db.update_user(u.id, u.name, not u.is_admin)
            self.db.log_action(self.current, "Права изменены",
                               f"{u.name or u.login}: " + ("теперь сотрудник" if u.is_admin else "теперь администратор"))
        except UserError as e:
            QMessageBox.warning(self, "Ошибка", str(e))
        self.refresh()

    def delete_user(self):
        u = self.selected()
        if not u:
            return
        if u.id == self.current.id:
            QMessageBox.warning(self, "Ошибка", "Нельзя удалить самого себя.")
            return
        answer = QMessageBox.question(
            self, "Удалить сотрудника",
            f"Удалить сотрудника «{u.name or u.login}»? Его доставки останутся.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.db.delete_user(u.id)
            self.db.log_action(self.current, "Сотрудник удалён", f"{u.name or u.login} (логин {u.login})")
        except UserError as e:
            QMessageBox.warning(self, "Ошибка", str(e))
        self.refresh()
