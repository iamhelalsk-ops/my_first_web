import hmac
import logging
import math
import os
import secrets
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for

from database import connect, initialize_database


CATEGORIES = [
    "Food", "Housing", "Transport", "Shopping", "Health", "Entertainment",
    "Utilities", "Education", "Salary", "Freelance", "Other",
]
PRIORITIES = ["Low", "Medium", "High"]


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SMARTLIFE_SECRET_KEY") or secrets.token_hex(32),
        DATABASE=str(Path(app.instance_path) / "smartlife.sqlite3"),
    )
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    initialize_database(app.config["DATABASE"])
    app.logger.setLevel(logging.INFO)
    app.jinja_env.filters["currency"] = lambda value: f"${value:,.2f}"

    @app.before_request
    def protect_forms() -> None:
        if request.method == "POST":
            submitted = request.form.get("csrf_token", "")
            expected = session.get("_csrf_token", "")
            if not expected or not hmac.compare_digest(submitted, expected):
                abort(400, description="Your form session expired. Refresh the page and try again.")

    @app.teardown_appcontext
    def close_database(_error: BaseException | None = None) -> None:
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    @app.context_processor
    def inject_template_values() -> dict:
        if "_csrf_token" not in session:
            session["_csrf_token"] = secrets.token_urlsafe(32)
        return {
            "csrf_token": session["_csrf_token"],
            "categories": CATEGORIES,
            "today": date.today().isoformat(),
        }

    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", message=getattr(error, "description", "Invalid request.")), 400

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", message="That page or record could not be found."), 404

    @app.errorhandler(sqlite3.Error)
    def database_error(error):
        app.logger.exception("Database operation failed: %s", error)
        return render_template(
            "error.html", message="We couldn't save that change. Please try again."
        ), 500

    def db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = connect(app.config["DATABASE"])
        return g.db

    def valid_date(value: str, field_name: str, *, optional: bool = False) -> str | None:
        if optional and not value.strip():
            return None
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field_name} must be a valid date.") from None
        if parsed.isoformat() != value:
            raise ValueError(f"{field_name} must be a valid date.")
        return value

    def valid_month(value: str) -> str:
        try:
            parsed = date.fromisoformat(f"{value}-01")
        except (TypeError, ValueError):
            raise ValueError("Choose a valid budget month.") from None
        if parsed.strftime("%Y-%m") != value:
            raise ValueError("Choose a valid budget month.")
        return value

    def valid_amount(value: str) -> float:
        try:
            amount = float(value)
        except (TypeError, ValueError):
            raise ValueError("Enter a valid amount.") from None
        if not (0 < amount < 1_000_000_000) or not math.isfinite(amount):
            raise ValueError("Amount must be greater than zero and less than 1,000,000,000.")
        return round(amount, 2)

    def add_budget_notices(month: str, category: str) -> None:
        rows = db().execute(
            "SELECT category, amount FROM budgets WHERE month = ? AND category IN (?, 'Overall')",
            (month, category),
        ).fetchall()
        for budget in rows:
            spent = db().execute(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE kind = 'expense' "
                "AND substr(transaction_date, 1, 7) = ? AND (? = 'Overall' OR category = ?)",
                (month, budget["category"], budget["category"]),
            ).fetchone()[0]
            if spent >= budget["amount"]:
                flash(f"{budget['category']} budget exceeded ({spent:.2f} spent).", "danger")
            elif spent >= budget["amount"] * 0.8:
                flash(f"{budget['category']} budget warning: {spent / budget['amount']:.0%} used.", "warning")

    def required_text(value: str, label: str, max_length: int = 120) -> str:
        result = value.strip()
        if not result:
            raise ValueError(f"{label} is required.")
        if len(result) > max_length:
            raise ValueError(f"{label} must be {max_length} characters or fewer.")
        return result

    def handle_form_error(error: ValueError, endpoint: str, **values):
        flash(str(error), "danger")
        return redirect(url_for(endpoint, **values))

    @app.get("/")
    def dashboard():
        today = date.today()
        month = today.strftime("%Y-%m")
        month_start = f"{month}-01"
        income = db().execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions "
            "WHERE kind = 'income' AND transaction_date >= ? AND transaction_date < date(?, '+1 month')",
            (month_start, month_start),
        ).fetchone()[0]
        expenses = db().execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions "
            "WHERE kind = 'expense' AND transaction_date >= ? AND transaction_date < date(?, '+1 month')",
            (month_start, month_start),
        ).fetchone()[0]
        budget_rows = db().execute(
            "SELECT category, amount FROM budgets WHERE month = ?", (month,)
        ).fetchall()
        overall = next((row["amount"] for row in budget_rows if row["category"] == "Overall"), None)
        budget_total = overall if overall is not None else sum(
            row["amount"] for row in budget_rows if row["category"] != "Overall"
        )
        pending_tasks = db().execute(
            "SELECT COUNT(*) FROM tasks WHERE completed = 0"
        ).fetchone()[0]
        completed_tasks = db().execute(
            "SELECT COUNT(*) FROM tasks WHERE completed = 1"
        ).fetchone()[0]
        weekly_rows = db().execute(
            "SELECT transaction_date, SUM(amount) AS total FROM transactions "
            "WHERE kind = 'expense' AND transaction_date >= ? AND transaction_date <= ? "
            "GROUP BY transaction_date",
            ((today - timedelta(days=6)).isoformat(), today.isoformat()),
        ).fetchall()
        weekly = {row["transaction_date"]: row["total"] for row in weekly_rows}
        week_labels = [(today - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)]
        recent = db().execute(
            "SELECT * FROM transactions ORDER BY transaction_date DESC, id DESC LIMIT 6"
        ).fetchall()
        reminders = db().execute(
            "SELECT * FROM tasks WHERE completed = 0 AND due_date IS NOT NULL "
            "AND due_date >= ? ORDER BY due_date LIMIT 5",
            (today.isoformat(),),
        ).fetchall()
        habits = db().execute(
            "SELECT COUNT(*) FROM habits"
        ).fetchone()[0]
        completed_habits = db().execute(
            "SELECT COUNT(*) FROM habit_logs WHERE log_date = ? AND completed = 1",
            (today.isoformat(),),
        ).fetchone()[0]
        return render_template(
            "dashboard.html", income=income, expenses=expenses, budget_total=budget_total,
            remaining=budget_total - expenses, pending_tasks=pending_tasks,
            completed_tasks=completed_tasks, recent=recent, reminders=reminders,
            week_labels=week_labels, week_values=[weekly.get(day, 0) for day in week_labels],
            habit_count=habits, habits_done=completed_habits,
        )

    @app.get("/transactions")
    def transactions():
        start = request.args.get("start", "").strip()
        end = request.args.get("end", "").strip()
        category = request.args.get("category", "").strip()
        kind = request.args.get("kind", "").strip()
        try:
            if start:
                valid_date(start, "Start date")
            if end:
                valid_date(end, "End date")
            if start and end and start > end:
                raise ValueError("Start date must be on or before end date.")
            if category and category not in CATEGORIES:
                raise ValueError("Choose a valid category.")
            if kind and kind not in ("income", "expense"):
                raise ValueError("Choose a valid transaction type.")
        except ValueError as error:
            flash(str(error), "danger")
            start = end = category = kind = ""

        clauses, params = [], []
        if start:
            clauses.append("transaction_date >= ?")
            params.append(start)
        if end:
            clauses.append("transaction_date <= ?")
            params.append(end)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = db().execute(
            f"SELECT * FROM transactions {where} ORDER BY transaction_date DESC, id DESC",
            params,
        ).fetchall()
        edit_id = request.args.get("edit", type=int)
        editing = db().execute("SELECT * FROM transactions WHERE id = ?", (edit_id,)).fetchone() if edit_id else None
        return render_template(
            "transactions.html", transactions=rows, editing=editing,
            filters={"start": start, "end": end, "category": category, "kind": kind},
        )

    @app.post("/transactions")
    def create_transaction():
        try:
            kind = request.form.get("kind", "")
            if kind not in ("income", "expense"):
                raise ValueError("Choose income or expense.")
            category = required_text(request.form.get("category", ""), "Category", 40)
            if category not in CATEGORIES:
                raise ValueError("Choose a valid category.")
            description = request.form.get("description", "").strip()
            if len(description) > 200:
                raise ValueError("Description must be 200 characters or fewer.")
            transaction_date = valid_date(request.form.get("transaction_date", ""), "Transaction date")
            amount = valid_amount(request.form.get("amount", ""))
            db().execute(
                "INSERT INTO transactions (kind, amount, category, description, transaction_date) "
                "VALUES (?, ?, ?, ?, ?)",
                (kind, amount, category, description, transaction_date),
            )
            db().commit()
            flash("Transaction added.", "success")
            if kind == "expense":
                add_budget_notices(transaction_date[:7], category)
            return redirect(url_for("transactions"))
        except ValueError as error:
            return handle_form_error(error, "transactions")

    @app.post("/transactions/<int:transaction_id>/edit")
    def edit_transaction(transaction_id: int):
        if not db().execute("SELECT 1 FROM transactions WHERE id = ?", (transaction_id,)).fetchone():
            abort(404)
        try:
            kind = request.form.get("kind", "")
            category = request.form.get("category", "")
            if kind not in ("income", "expense") or category not in CATEGORIES:
                raise ValueError("Choose a valid transaction type and category.")
            description = request.form.get("description", "").strip()
            if len(description) > 200:
                raise ValueError("Description must be 200 characters or fewer.")
            db().execute(
                "UPDATE transactions SET kind = ?, amount = ?, category = ?, description = ?, "
                "transaction_date = ? WHERE id = ?",
                (kind, valid_amount(request.form.get("amount", "")), category, description,
                 valid_date(request.form.get("transaction_date", ""), "Transaction date"), transaction_id),
            )
            db().commit()
            flash("Transaction updated.", "success")
            if kind == "expense":
                transaction_date = request.form.get("transaction_date", "")
                add_budget_notices(transaction_date[:7], category)
            return redirect(url_for("transactions"))
        except ValueError as error:
            return handle_form_error(error, "transactions")

    @app.post("/transactions/<int:transaction_id>/delete")
    def delete_transaction(transaction_id: int):
        result = db().execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
        if not result.rowcount:
            abort(404)
        db().commit()
        flash("Transaction deleted.", "success")
        return redirect(url_for("transactions"))

    @app.get("/budgets")
    def budgets():
        month = request.args.get("month", date.today().strftime("%Y-%m"))
        try:
            month = valid_month(month)
        except ValueError as error:
            flash(str(error), "danger")
            month = date.today().strftime("%Y-%m")
        rows = db().execute(
            "SELECT b.*, COALESCE(SUM(t.amount), 0) AS spent FROM budgets b "
            "LEFT JOIN transactions t ON (b.category = 'Overall' OR t.category = b.category) "
            "AND t.kind = 'expense' "
            "AND substr(t.transaction_date, 1, 7) = b.month "
            "WHERE b.month = ? GROUP BY b.id ORDER BY b.category",
            (month,),
        ).fetchall()
        return render_template("budgets.html", budgets=rows, selected_month=month)

    @app.post("/budgets")
    def save_budget():
        try:
            month = valid_month(request.form.get("month", ""))
            category = request.form.get("category", "Overall").strip() or "Overall"
            if category != "Overall" and category not in CATEGORIES:
                raise ValueError("Choose a valid category.")
            amount = valid_amount(request.form.get("amount", ""))
            db().execute(
                "INSERT INTO budgets (month, category, amount) VALUES (?, ?, ?) "
                "ON CONFLICT(month, category) DO UPDATE SET amount = excluded.amount",
                (month, category, amount),
            )
            db().commit()
            spent = db().execute(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE kind = 'expense' "
                "AND substr(transaction_date, 1, 7) = ? AND (? = 'Overall' OR category = ?)",
                (month, category, category),
            ).fetchone()[0]
            if spent >= amount:
                flash(f"Budget exceeded: {spent:.2f} spent against {amount:.2f}.", "danger")
            elif spent >= amount * 0.8:
                flash(f"Budget warning: {spent:.2f} spent ({spent / amount:.0%} of the limit).", "warning")
            else:
                flash("Budget saved.", "success")
            return redirect(url_for("budgets", month=month))
        except ValueError as error:
            return handle_form_error(error, "budgets")

    @app.post("/budgets/<int:budget_id>/delete")
    def delete_budget(budget_id: int):
        row = db().execute("SELECT month FROM budgets WHERE id = ?", (budget_id,)).fetchone()
        if row is None:
            abort(404)
        db().execute("DELETE FROM budgets WHERE id = ?", (budget_id,))
        db().commit()
        flash("Budget deleted.", "success")
        return redirect(url_for("budgets", month=row["month"]))

    @app.get("/tasks")
    def tasks():
        status = request.args.get("status", "all")
        priority = request.args.get("priority", "")
        if status not in ("all", "pending", "completed"):
            status = "all"
            flash("Invalid task status filter.", "danger")
        if priority and priority not in PRIORITIES:
            priority = ""
            flash("Invalid priority filter.", "danger")
        clauses, params = [], []
        if status == "pending":
            clauses.append("completed = 0")
        elif status == "completed":
            clauses.append("completed = 1")
        if priority:
            clauses.append("priority = ?")
            params.append(priority)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = db().execute(
            f"SELECT * FROM tasks {where} ORDER BY completed, "
            "CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, due_date",
            params,
        ).fetchall()
        edit_id = request.args.get("edit", type=int)
        editing = db().execute("SELECT * FROM tasks WHERE id = ?", (edit_id,)).fetchone() if edit_id else None
        return render_template(
            "tasks.html", tasks=rows, editing=editing, status=status,
            selected_priority=priority, priorities=PRIORITIES,
        )

    def task_values():
        title = required_text(request.form.get("title", ""), "Task title", 120)
        description = request.form.get("description", "").strip()
        if len(description) > 500:
            raise ValueError("Description must be 500 characters or fewer.")
        priority = request.form.get("priority", "")
        if priority not in PRIORITIES:
            raise ValueError("Choose a valid priority.")
        due_date = valid_date(request.form.get("due_date", ""), "Due date", optional=True)
        return title, description, priority, due_date

    @app.post("/tasks")
    def create_task():
        try:
            db().execute(
                "INSERT INTO tasks (title, description, priority, due_date) VALUES (?, ?, ?, ?)",
                task_values(),
            )
            db().commit()
            flash("Task created.", "success")
            return redirect(url_for("tasks"))
        except ValueError as error:
            return handle_form_error(error, "tasks")

    @app.post("/tasks/<int:task_id>/edit")
    def edit_task(task_id: int):
        if not db().execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone():
            abort(404)
        try:
            db().execute(
                "UPDATE tasks SET title = ?, description = ?, priority = ?, due_date = ? WHERE id = ?",
                (*task_values(), task_id),
            )
            db().commit()
            flash("Task updated.", "success")
            return redirect(url_for("tasks"))
        except ValueError as error:
            return handle_form_error(error, "tasks")

    @app.post("/tasks/<int:task_id>/toggle")
    def toggle_task(task_id: int):
        result = db().execute(
            "UPDATE tasks SET completed = CASE completed WHEN 1 THEN 0 ELSE 1 END WHERE id = ?",
            (task_id,),
        )
        if not result.rowcount:
            abort(404)
        db().commit()
        flash("Task status updated.", "success")
        return redirect(url_for("tasks"))

    @app.post("/tasks/<int:task_id>/delete")
    def delete_task(task_id: int):
        result = db().execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        if not result.rowcount:
            abort(404)
        db().commit()
        flash("Task deleted.", "success")
        return redirect(url_for("tasks"))

    @app.get("/habits")
    def habits():
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        rows = db().execute(
            "SELECT h.id, h.name, EXISTS(SELECT 1 FROM habit_logs l WHERE l.habit_id = h.id "
            "AND l.log_date = ? AND l.completed = 1) AS done, "
            "(SELECT COUNT(*) FROM habit_logs l WHERE l.habit_id = h.id AND l.log_date >= ? "
            "AND l.log_date <= ? AND l.completed = 1) AS weekly_done FROM habits h ORDER BY h.name",
            (today.isoformat(), week_start.isoformat(), today.isoformat()),
        ).fetchall()
        return render_template(
            "habits.html", habits=rows, week_start=week_start,
            weekly_days=(today - week_start).days + 1,
        )

    @app.post("/habits")
    def create_habit():
        try:
            name = required_text(request.form.get("name", ""), "Habit name", 80)
            try:
                db().execute("INSERT INTO habits (name) VALUES (?)", (name,))
                db().commit()
            except sqlite3.IntegrityError:
                db().rollback()
                raise ValueError("A habit with that name already exists.") from None
            flash("Habit created.", "success")
            return redirect(url_for("habits"))
        except ValueError as error:
            return handle_form_error(error, "habits")

    @app.post("/habits/<int:habit_id>/toggle")
    def toggle_habit(habit_id: int):
        if not db().execute("SELECT 1 FROM habits WHERE id = ?", (habit_id,)).fetchone():
            abort(404)
        today = date.today().isoformat()
        db().execute(
            "INSERT INTO habit_logs (habit_id, log_date, completed) VALUES (?, ?, 1) "
            "ON CONFLICT(habit_id, log_date) DO UPDATE SET "
            "completed = CASE completed WHEN 1 THEN 0 ELSE 1 END",
            (habit_id, today),
        )
        db().commit()
        flash("Today's habit progress updated.", "success")
        return redirect(url_for("habits"))

    @app.post("/habits/<int:habit_id>/delete")
    def delete_habit(habit_id: int):
        result = db().execute("DELETE FROM habits WHERE id = ?", (habit_id,))
        if not result.rowcount:
            abort(404)
        db().commit()
        flash("Habit deleted.", "success")
        return redirect(url_for("habits"))

    @app.get("/reports")
    def reports():
        today = date.today()
        year = request.args.get("year", str(today.year))
        if not year.isdigit() or len(year) != 4 or not 2000 <= int(year) <= 2100:
            flash("Choose a year between 2000 and 2100.", "danger")
            year = str(today.year)
        year_int = int(year)
        monthly_rows = db().execute(
            "SELECT substr(transaction_date, 1, 7) AS month, kind, SUM(amount) AS total "
            "FROM transactions WHERE transaction_date >= ? AND transaction_date < ? "
            "GROUP BY month, kind",
            (f"{year}-01-01", f"{year_int + 1}-01-01"),
        ).fetchall()
        monthly = {(row["month"], row["kind"]): row["total"] for row in monthly_rows}
        month_labels = [date(year_int, month, 1).strftime("%b") for month in range(1, 13)]
        income_values = [monthly.get((f"{year}-{month:02d}", "income"), 0) for month in range(1, 13)]
        expense_values = [monthly.get((f"{year}-{month:02d}", "expense"), 0) for month in range(1, 13)]
        selected_month = today.strftime("%Y-%m") if year == str(today.year) else f"{year}-12"
        category_rows = db().execute(
            "SELECT category, SUM(amount) AS total FROM transactions "
            "WHERE kind = 'expense' AND substr(transaction_date, 1, 7) = ? "
            "GROUP BY category ORDER BY total DESC",
            (selected_month,),
        ).fetchall()
        week_start = today - timedelta(days=6)
        weekly_rows = db().execute(
            "SELECT transaction_date, SUM(amount) AS total FROM transactions "
            "WHERE kind = 'expense' AND transaction_date >= ? AND transaction_date <= ? "
            "GROUP BY transaction_date",
            (week_start.isoformat(), today.isoformat()),
        ).fetchall()
        weekly = {row["transaction_date"]: row["total"] for row in weekly_rows}
        week_days = [(week_start + timedelta(days=offset)).isoformat() for offset in range(7)]
        budget_rows = db().execute(
            "SELECT b.category, b.amount, COALESCE(SUM(t.amount), 0) AS spent FROM budgets b "
            "LEFT JOIN transactions t ON t.kind = 'expense' "
            "AND (b.category = 'Overall' OR t.category = b.category) "
            "AND substr(t.transaction_date, 1, 7) = b.month WHERE b.month = ? "
            "GROUP BY b.id ORDER BY b.category",
            (selected_month,),
        ).fetchall()
        return render_template(
            "reports.html", year=year, selected_month=selected_month,
            month_labels=month_labels, income_values=income_values, expense_values=expense_values,
            category_labels=[row["category"] for row in category_rows],
            category_values=[row["total"] for row in category_rows],
            week_labels=week_days, week_values=[weekly.get(day, 0) for day in week_days],
            budget_labels=[row["category"] for row in budget_rows],
            budget_values=[row["amount"] for row in budget_rows],
            budget_spent=[row["spent"] for row in budget_rows],
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
