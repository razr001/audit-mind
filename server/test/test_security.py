from unittest import TestCase
from uuid import UUID

from app.core.security import create_token, decode_and_verify_jwt


class Test(TestCase):
    def test_create_token(self):
        token = create_token(UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59"), "timou")
        self.assertIsNot(token, None)

    def test_decode_and_verify_jwt(self):
        token = create_token(UUID("9efa0f2d-7e1f-4204-8d0e-f254e36c8e59"), "timou")
        payload = decode_and_verify_jwt(token)
        self.assertEqual(payload["sub"], "9efa0f2d-7e1f-4204-8d0e-f254e36c8e59")
        self.assertEqual(payload["username"], "timou")
        self.assertEqual(payload["token_type"], "access")
