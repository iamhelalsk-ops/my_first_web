import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import create_app
from database import connect


class SmartLifeTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp_dir.name) / "test.sqlite3")
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "DATABASE": self.database_path,
        })
        self.client = self.app.test_client()
        self.today = date.today().isoformat()
        self.month = self.today[:7]
        self.client.get("/")
        with self.client.session_transaction() as session:
            self.csrf = session["_csrf_token"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def post(self, url, data=None, **kwargs):
        values = {"csrf_token": self.csrf}
        values.update(data or {})
        return self.client.post(url, data=values, **kwargs)

    def rows(self, sql, params=()):
        connection = connect(self.database_path)
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def test_all_pages_render(self):
        for path in ("/", "/transactions", "/budgets", "/tasks", "/habits", "/reports"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_transaction_create_filter_edit_delete_and_invalid_amount(self):
        response = self.post("/transactions", {
            "kind": "expense", "amount": "125.50", "category": "Food",
            "description": "Market", "transaction_date": self.today,
        })
        self.assertEqual(response.status_code, 302)
        transaction_id = self.rows("SELECT id FROM transactions")[0]["id"]
        filtered = self.client.get(
            f"/transactions?start={self.month}-01&end={self.today}&category=Food"
        )
        self.assertIn(b"Market", filtered.data)

        self.post(f"/transactions/{transaction_id}/edit", {
            "kind": "income", "amount": "200", "category": "Salary",
            "description": "Refund", "transaction_date": self.today,
        })
        row = self.rows("SELECT * FROM transactions WHERE id = ?", (transaction_id,))[0]
        self.assertEqual((row["kind"], row["amount"], row["category"]), ("income", 200, "Salary"))

        self.post("/transactions", {
            "kind": "expense", "amount": "0", "category": "Food",
            "description": "", "transaction_date": self.today,
        })
        self.assertEqual(len(self.rows("SELECT id FROM transactions")), 1)
        self.post(f"/transactions/{transaction_id}/delete")
        self.assertEqual(len(self.rows("SELECT id FROM transactions")), 0)

    def test_budgets_and_dashboard_calculations(self):
        self.post("/transactions", {
            "kind": "expense", "amount": "80", "category": "Food",
            "description": "Groceries", "transaction_date": self.today,
        })
        self.post("/budgets", {"month": self.month, "category": "Overall", "amount": "100"})
        self.post("/budgets", {"month": self.month, "category": "Food", "amount": "90"})
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("$80.00", body)
        self.assertIn("$20.00", body)
        budget_page = self.client.get(f"/budgets?month={self.month}").get_data(as_text=True)
        self.assertIn("Almost at your limit", budget_page)
        self.post("/budgets/1/delete")
        self.assertEqual(len(self.rows("SELECT id FROM budgets")), 1)

    def test_task_create_filter_toggle_edit_delete(self):
        self.post("/tasks", {
            "title": "Write tests", "description": "Cover the app", "priority": "High",
            "due_date": "2026-11-10",
        })
        task_id = self.rows("SELECT id FROM tasks")[0]["id"]
        self.assertIn(b"Write tests", self.client.get("/tasks?priority=High&status=pending").data)
        self.post(f"/tasks/{task_id}/toggle")
        self.assertEqual(self.rows("SELECT completed FROM tasks")[0]["completed"], 1)
        self.post(f"/tasks/{task_id}/edit", {
            "title": "Ship tests", "description": "", "priority": "Medium", "due_date": "",
        })
        self.assertEqual(self.rows("SELECT title, priority FROM tasks")[0][:], ("Ship tests", "Medium"))
        self.post(f"/tasks/{task_id}/delete")
        self.assertEqual(len(self.rows("SELECT id FROM tasks")), 0)

    def test_habit_tracking_and_cascade_delete(self):
        self.post("/habits", {"name": "Read daily"})
        habit_id = self.rows("SELECT id FROM habits")[0]["id"]
        self.post(f"/habits/{habit_id}/toggle")
        self.assertEqual(self.rows("SELECT completed FROM habit_logs")[0]["completed"], 1)
        self.assertIn(b"1 of", self.client.get("/habits").data)
        self.post(f"/habits/{habit_id}/toggle")
        self.assertEqual(self.rows("SELECT completed FROM habit_logs")[0]["completed"], 0)
        self.post(f"/habits/{habit_id}/delete")
        self.assertEqual(len(self.rows("SELECT id FROM habit_logs")), 0)

    def test_invalid_dates_and_csrf_are_rejected(self):
        response = self.post("/transactions", {
            "kind": "expense", "amount": "5", "category": "Food",
            "description": "", "transaction_date": "not-a-date",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.rows("SELECT id FROM transactions")), 0)
        self.assertEqual(self.client.post("/tasks", data={"title": "No token"}).status_code, 400)
        self.assertEqual(self.client.get("/transactions?start=2026-12-01&end=2026-01-01").status_code, 200)

    def test_persistence_reports_and_dashboard_records(self):
        self.post("/transactions", {
            "kind": "income", "amount": "900", "category": "Salary",
            "description": "Pay", "transaction_date": self.today,
        })
        restarted_app = create_app({
            "TESTING": True, "SECRET_KEY": "test-key", "DATABASE": self.database_path,
        })
        response = restarted_app.test_client().get(f"/reports?year={self.today[:4]}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Income vs. expenses", response.data)
        self.assertIn(b"Pay", restarted_app.test_client().get("/").data)


if __name__ == "__main__":
    unittest.main()
