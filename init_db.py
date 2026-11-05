import argparse
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from database import connect, initialize_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize SmartLife's SQLite database.")
    parser.add_argument("--demo", action="store_true", help="Add a small set of sample records.")
    parser.add_argument(
        "--database",
        default=str(Path(__file__).parent / "instance" / "smartlife.sqlite3"),
        help="Database file path (defaults to instance/smartlife.sqlite3).",
    )
    args = parser.parse_args()
    initialize_database(args.database)

    if args.demo:
        today = date.today()
        month = today.strftime("%Y-%m")
        with closing(connect(args.database)) as connection, connection:
            has_data = any(
                connection.execute(f"SELECT EXISTS(SELECT 1 FROM {table})").fetchone()[0]
                for table in ("transactions", "budgets", "tasks", "habits")
            )
            if has_data:
                print("Demo data was not added because this database already contains records.")
                return

            transactions = [
                ("income", 3200, "Salary", "Monthly salary", today.replace(day=2).isoformat()),
                ("expense", 58.40, "Food", "Groceries", today.isoformat()),
                ("expense", 24.00, "Transport", "Metro pass", (today - timedelta(days=1)).isoformat()),
                ("expense", 92.15, "Utilities", "Electricity bill", (today - timedelta(days=3)).isoformat()),
                ("expense", 36.70, "Food", "Lunch with friends", (today - timedelta(days=5)).isoformat()),
            ]
            connection.executemany(
                "INSERT INTO transactions (kind, amount, category, description, transaction_date) "
                "VALUES (?, ?, ?, ?, ?)",
                transactions,
            )
            connection.executemany(
                "INSERT INTO budgets (month, category, amount) VALUES (?, ?, ?)",
                [(month, "Overall", 1200), (month, "Food", 300), (month, "Transport", 150)],
            )
            connection.executemany(
                "INSERT INTO tasks (title, description, priority, due_date) VALUES (?, ?, ?, ?)",
                [
                    ("Plan the week", "Choose the three most important things to focus on.", "High",
                     (today + timedelta(days=1)).isoformat()),
                    ("Book a checkup", "", "Medium", (today + timedelta(days=4)).isoformat()),
                    ("Tidy up your workspace", "", "Low", None),
                ],
            )
            connection.executemany(
                "INSERT INTO habits (name) VALUES (?)",
                [("Read for 10 minutes",), ("Take a short walk",), ("Drink enough water",)],
            )
            habit_ids = connection.execute("SELECT id FROM habits ORDER BY id").fetchall()
            connection.executemany(
                "INSERT INTO habit_logs (habit_id, log_date, completed) VALUES (?, ?, 1)",
                [(row["id"], today.isoformat()) for row in habit_ids[:2]],
            )
        print(f"Database initialized and demo data added: {args.database}")
    else:
        print(f"Database initialized: {args.database}")


if __name__ == "__main__":
    main()
