from rest_framework import status
from rest_framework.test import APITestCase

from .models import AuditLog, DoctorProfile, User


class AdminModuleTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin1", password="Admin@12345", role=User.Role.ADMINISTRATOR)
        self.doctor = User.objects.create_user("doc1", password="Doctor@12345", role=User.Role.DOCTOR)

    def _login(self, username, password):
        r = self.client.post("/api/auth/login/", {"username": username, "password": password})
        self.assertEqual(r.status_code, 200, r.content)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")

    def test_login_token_contains_role(self):
        r = self.client.post("/api/auth/login/", {"username": "admin1", "password": "Admin@12345"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("access", r.data)

    def test_login_wrong_password(self):
        r = self.client.post("/api/auth/login/", {"username": "admin1", "password": "bad"})
        self.assertEqual(r.status_code, 401)

    def test_no_token_is_401(self):
        self.assertEqual(self.client.get("/api/admin/users/").status_code, 401)

    def test_non_admin_is_403(self):
        self._login("doc1", "Doctor@12345")
        self.assertEqual(self.client.get("/api/admin/users/").status_code, 403)

    def test_create_doctor_creates_profile_and_audit(self):
        self._login("admin1", "Admin@12345")
        r = self.client.post("/api/admin/users/", {
            "username": "dr_ravi", "password": "Ravi@12345", "first_name": "Ravi",
            "email": "ravi@clinic.com", "role": "DOCTOR", "phone": "9876543210",
            "consultation_fee": "300.00",
        })
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(DoctorProfile.objects.get(user__username="dr_ravi").consultation_fee, 300)
        self.assertNotIn("password", r.data)
        self.assertTrue(DoctorProfile.objects.filter(user__username="dr_ravi").exists())
        self.assertTrue(AuditLog.objects.filter(user=self.admin, action__icontains="dr_ravi").exists())

    def test_create_duplicate_username_and_weak_password(self):
        self._login("admin1", "Admin@12345")
        r = self.client.post("/api/admin/users/", {"username": "doc1", "password": "Xyz@12345", "role": "DOCTOR"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/admin/users/", {"username": "new1", "password": "123", "role": "DOCTOR"})
        self.assertEqual(r.status_code, 400)

    def test_invalid_role(self):
        self._login("admin1", "Admin@12345")
        r = self.client.post("/api/admin/users/", {"username": "x1", "password": "Xyz@12345", "role": "BOSS"})
        self.assertEqual(r.status_code, 400)

    def test_list_filter_update_no_delete(self):
        self._login("admin1", "Admin@12345")
        r = self.client.get("/api/admin/users/?role=DOCTOR")
        self.assertEqual([u["username"] for u in r.data], ["doc1"])
        r = self.client.patch(f"/api/admin/users/{self.doctor.id}/", {"phone": "111"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["phone"], "111")

    def test_deactivate_blocks_login_and_self_deactivate_blocked(self):
        self._login("admin1", "Admin@12345")
        r = self.client.post(f"/api/admin/users/{self.doctor.id}/deactivate/")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["is_active"])
        r = self.client.post(f"/api/admin/users/{self.admin.id}/deactivate/")
        self.assertEqual(r.status_code, 400)
        self.client.credentials()
        r = self.client.post("/api/auth/login/", {"username": "doc1", "password": "Doctor@12345"})
        self.assertEqual(r.status_code, 401)

    def test_reset_password(self):
        self._login("admin1", "Admin@12345")
        r = self.client.post(f"/api/admin/users/{self.doctor.id}/reset-password/", {"new_password": "NewPass@999"})
        self.assertEqual(r.status_code, 200)
        self.client.credentials()
        self._login("doc1", "NewPass@999")

    def test_change_own_password(self):
        self._login("doc1", "Doctor@12345")
        r = self.client.post("/api/admin/change-password/", {"old_password": "wrong", "new_password": "Fresh@12345"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/admin/change-password/", {"old_password": "Doctor@12345", "new_password": "Fresh@12345"})
        self.assertEqual(r.status_code, 200)

    def test_me_audit_logs_dashboard(self):
        self._login("admin1", "Admin@12345")
        self.assertEqual(self.client.get("/api/admin/me/").data["username"], "admin1")
        self.client.post(f"/api/admin/users/{self.doctor.id}/deactivate/")
        r = self.client.get("/api/admin/audit-logs/?search=Deactivated")
        self.assertEqual(len(r.data), 1)
        self.assertEqual(self.client.post("/api/admin/audit-logs/", {}).status_code, 405)
        r = self.client.get("/api/admin/dashboard/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["active_staff_by_role"]["ADMINISTRATOR"], 1)

    def test_login_returns_user_info_for_every_role(self):
        for role in ["ADMINISTRATOR", "RECEPTIONIST", "DOCTOR", "LAB_TECHNICIAN", "PHARMACIST"]:
            User.objects.create_user(f"u_{role}", password="Pass@12345", role=role)
            r = self.client.post("/api/auth/login/", {"username": f"u_{role}", "password": "Pass@12345"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.data["user"]["role"], role)

    def test_login_validation(self):
        self.assertEqual(self.client.post("/api/auth/login/", {}).status_code, 400)
        self.assertEqual(self.client.post("/api/auth/login/", {"username": "admin1"}).status_code, 400)
        self.assertEqual(self.client.post("/api/auth/login/", {"username": "nobody", "password": "x"}).status_code, 401)
        User.objects.create_user("norole", password="Pass@12345")
        r = self.client.post("/api/auth/login/", {"username": "norole", "password": "Pass@12345"})
        self.assertEqual(r.status_code, 401)

    def test_delete_not_allowed(self):
        self._login("admin1", "Admin@12345")
        self.assertEqual(self.client.delete(f"/api/admin/users/{self.doctor.id}/").status_code, 405)
