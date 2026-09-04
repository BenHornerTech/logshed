"""
Tests for secret scrubbing regexes and on-demand redaction (sanitizer.py).
"""

from app.core.sanitizer import sanitize, REDACTED


class TestSanitizer:

    def test_bearer_token_redacted(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxx.yyy"
        result = sanitize(text)
        assert "eyJ" not in result
        assert REDACTED in result
        assert "Authorization" in result

    def test_basic_auth_redacted(self):
        text = "Authorization: Basic dXNlcjpwYXNzd29yZA=="
        result = sanitize(text)
        assert "dXNlcjpwYXNzd29yZA==" not in result
        assert REDACTED in result

    def test_api_key_redacted(self):
        text = "api_key=sk-1234567890abcdef1234567890abcdef"
        result = sanitize(text)
        assert "sk-1234567890abcdef" not in result
        assert REDACTED in result

    def test_api_key_colon_format(self):
        text = "API-KEY: my_super_secret_key_value_12345678"
        result = sanitize(text)
        assert "my_super_secret_key_value_12345678" not in result
        assert REDACTED in result

    def test_password_field_redacted(self):
        text = "password=MyS3cretP@ss!"
        result = sanitize(text)
        assert "MyS3cretP@ss!" not in result
        assert REDACTED in result

    def test_password_colon_format(self):
        text = "password: hunter2"
        result = sanitize(text)
        assert "hunter2" not in result
        assert REDACTED in result

    def test_token_field_redacted(self):
        text = 'access_token=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
        result = sanitize(text)
        assert "ghp_" not in result
        assert REDACTED in result

    def test_jwt_standalone_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        text = f"Token is {jwt}"
        result = sanitize(text)
        assert "eyJhbGciOiJIUzI1NiI" not in result
        assert REDACTED in result

    def test_aws_access_key_redacted(self):
        text = "AWS key is AKIAIOSFODNN7EXAMPLE"
        result = sanitize(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert REDACTED in result

    def test_aws_secret_key_redacted(self):
        text = "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY12"
        result = sanitize(text)
        assert "wJalrXUtnFEMI" not in result
        assert REDACTED in result

    def test_private_key_redacted(self):
        text = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfnNkGFOQCPO...
-----END RSA PRIVATE KEY-----"""
        result = sanitize(text)
        assert "MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfnNkGFOQCPO" not in result
        assert REDACTED in result

    def test_connection_string_password_redacted(self):
        text = "postgres://admin:s3cret_pass@db.example.com:5432/mydb"
        result = sanitize(text)
        assert "s3cret_pass" not in result
        assert REDACTED in result
        assert "db.example.com" in result

    def test_pushover_token_redacted(self):
        text = "pushover_app_token=azGDORePK8gMaC0QOYAMyEEuzJnyUi"
        result = sanitize(text)
        assert "azGDORePK8gMaC0QOYAMyEEuzJnyUi" not in result
        assert REDACTED in result

    def test_normal_text_unchanged(self):
        text = "INFO: Container nginx started successfully on port 8080"
        result = sanitize(text)
        assert result == text

    def test_sanitize_list(self):
        lines = [
            "Normal log line",
            "api_key=secret12345678",
            "password=hunter2",
        ]
        result = sanitize(lines)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0] == "Normal log line"
        assert "secret12345678" not in result[1]
        assert "hunter2" not in result[2]

    def test_multiple_secrets_in_one_line(self):
        text = "Authorization: Bearer tok123abc password=mypass api_key=sk-abc12345678"
        result = sanitize(text)
        assert "tok123abc" not in result
        assert "mypass" not in result
        assert "sk-abc12345678" not in result

    def test_x_api_key_header(self):
        text = "X-API-Key: my-secret-api-key-12345"
        result = sanitize(text)
        assert "my-secret-api-key-12345" not in result
        assert REDACTED in result
