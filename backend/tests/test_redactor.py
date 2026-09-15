"""
Tests for secret scrubbing regexes and on-demand redaction (redactor.py).
"""

from app.core.redactor import redact, REDACTED


class TestRedactor:

    def test_bearer_token_redacted(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxx.yyy"
        result = redact(text)
        assert "eyJ" not in result
        assert REDACTED in result
        assert "Authorization" in result

    def test_basic_auth_redacted(self):
        text = "Authorization: Basic dXNlcjpwYXNzd29yZA=="
        result = redact(text)
        assert "dXNlcjpwYXNzd29yZA==" not in result
        assert REDACTED in result

    def test_api_key_redacted(self):
        text = "api_key=sk-1234567890abcdef1234567890abcdef"
        result = redact(text)
        assert "sk-1234567890abcdef" not in result
        assert REDACTED in result

    def test_api_key_colon_format(self):
        text = "API-KEY: my_super_secret_key_value_12345678"
        result = redact(text)
        assert "my_super_secret_key_value_12345678" not in result
        assert REDACTED in result

    def test_password_field_redacted(self):
        text = "password=MyS3cretP@ss!"
        result = redact(text)
        assert "MyS3cretP@ss!" not in result
        assert REDACTED in result

    def test_password_colon_format(self):
        text = "password: hunter2"
        result = redact(text)
        assert "hunter2" not in result
        assert REDACTED in result

    def test_token_field_redacted(self):
        text = 'access_token=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
        result = redact(text)
        assert "ghp_" not in result
        assert REDACTED in result

    def test_jwt_standalone_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        text = f"Token is {jwt}"
        result = redact(text)
        assert "eyJhbGciOiJIUzI1NiI" not in result
        assert REDACTED in result

    def test_aws_access_key_redacted(self):
        text = "AWS key is AKIAIOSFODNN7EXAMPLE"
        result = redact(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert REDACTED in result

    def test_aws_secret_key_redacted(self):
        text = "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY12"
        result = redact(text)
        assert "wJalrXUtnFEMI" not in result
        assert REDACTED in result

    def test_private_key_redacted(self):
        text = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfnNkGFOQCPO...
-----END RSA PRIVATE KEY-----"""
        result = redact(text)
        assert "MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfnNkGFOQCPO" not in result
        assert REDACTED in result

    def test_connection_string_password_redacted(self):
        text = "postgres://admin:s3cret_pass@db.example.com:5432/mydb"
        result = redact(text)
        assert "s3cret_pass" not in result
        assert REDACTED in result
        assert "db.example.com" in result

    def test_connection_string_with_at_symbol_in_password(self):
        text = "Connecting to postgres://postgres:P@ssw0rd123!@db.internal:5432/prod_db with api_key=sk-proj-9999888877776666555544443333"
        result = redact(text)
        assert "P@ssw0rd123!" not in result
        assert "@ssw0rd123!" not in result
        assert result == f"Connecting to postgres://postgres:{REDACTED}@db.internal:5432/prod_db with api_key={REDACTED}"

    def test_pushover_token_redacted(self):
        text = "pushover_app_token=appToken1234567890abcdefghijklm"
        result = redact(text)
        assert "appToken1234567890abcdefghijklm" not in result
        assert REDACTED in result

    def test_normal_text_unchanged(self):
        text = "INFO: Container nginx started successfully on port 8080"
        result = redact(text)
        assert result == text

    def test_redact_list(self):
        lines = [
            "Normal log line",
            "api_key=secret12345678",
            "password=hunter2",
        ]
        result = redact(lines)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0] == "Normal log line"
        assert "secret12345678" not in result[1]
        assert "hunter2" not in result[2]

    def test_multiple_secrets_in_one_line(self):
        text = "Authorization: Bearer tok123abc password=mypass api_key=sk-abc12345678"
        result = redact(text)
        assert "tok123abc" not in result
        assert "mypass" not in result
        assert "sk-abc12345678" not in result

    def test_x_api_key_header(self):
        text = "X-API-Key: my-secret-api-key-12345"
        result = redact(text)
        assert "my-secret-api-key-12345" not in result
        assert REDACTED in result

    def test_redis_connection_string_without_username_redacted(self):
        text = "Connecting to redis://:mypassword@localhost:6379/0..."
        result = redact(text)
        assert "mypassword" not in result
        assert REDACTED in result
        assert result == f"Connecting to redis://:{REDACTED}@localhost:6379/0..."

    def test_sk_token_standalone_redacted(self):
        token = "sk-proj-abc1234567890abcdef12345678"
        text = f"API request using OpenAI key {token} failed"
        result = redact(text)
        assert token not in result
        assert REDACTED in result
        assert result == f"API request using OpenAI key {REDACTED} failed"

    def test_github_pat_standalone_redacted(self):
        # ghp_ followed by exactly 36 alphanumeric characters (total length 40)
        token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
        assert len(token) == 40
        text = f"git clone error with token {token}"
        result = redact(text)
        assert token not in result
        assert REDACTED in result
        assert result == f"git clone error with token {REDACTED}"

    def test_curl_auth_redacted(self):
        text1 = "curl -u admin:s3cretPassword123 https://api.internal/v1"
        result1 = redact(text1)
        assert "s3cretPassword123" not in result1
        assert result1 == f"curl -u admin:{REDACTED} https://api.internal/v1"

        text2 = "curl --user deploy:verySecretKey456 https://api.internal/v1"
        result2 = redact(text2)
        assert "verySecretKey456" not in result2
        assert result2 == f"curl --user deploy:{REDACTED} https://api.internal/v1"

    def test_slack_webhook_redacted(self):
        url = "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
        text = f"Sending alert to {url} failed with status 500"
        result = redact(text)
        assert "T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX" not in result
        assert REDACTED in result
        assert "https://hooks.slack.com/services/[REDACTED]" in result

    def test_generic_secret_assignment_redacted(self):
        text_double = 'secret="my_super_secret_double_quotes"'
        result_double = redact(text_double)
        assert "my_super_secret_double_quotes" not in result_double
        assert REDACTED in result_double

        text_single = "secret='my_super_secret_single_quotes'"
        result_single = redact(text_single)
        assert "my_super_secret_single_quotes" not in result_single
        assert REDACTED in result_single

        text_client = 'client_secret="oauth2_client_secret_xyz"'
        result_client = redact(text_client)
        assert "oauth2_client_secret_xyz" not in result_client
        assert REDACTED in result_client

        text_unquoted = "secret=my_unquoted_secret_value"
        result_unquoted = redact(text_unquoted)
        assert "my_unquoted_secret_value" not in result_unquoted
        assert REDACTED in result_unquoted

    def test_sshpass_command_redacted(self):
        text1 = "sshpass -p 'MyS3cretPassword123' ssh user@192.168.1.10"
        result1 = redact(text1)
        assert "MyS3cretPassword123" not in result1
        assert REDACTED in result1
        assert result1 == f"sshpass -p {REDACTED} ssh user@192.168.1.10"

        text2 = 'sshpass -p "AnotherSecretPass" scp file.txt host:/tmp'
        result2 = redact(text2)
        assert "AnotherSecretPass" not in result2
        assert REDACTED in result2
        assert result2 == f"sshpass -p {REDACTED} scp file.txt host:/tmp"

        text3 = "ssh -p 22 user@host"
        result3 = redact(text3)
        assert result3 == text3

    def test_docker_registry_auth_redacted(self):
        text_header = "X-Registry-Auth: eyJ1c2VybmFtZSI6ImFkbWluIiwicGFzc3dvcmQiOiJzZWNyZXQifQ=="
        result_header = redact(text_header)
        assert "eyJ1c2VybmFtZSI6ImFkbWluIiwicGFzc3dvcmQiOiJzZWNyZXQifQ==" not in result_header
        assert REDACTED in result_header

        text_token = "X-Registry-Token: reg_token_abcdef123456"
        result_token = redact(text_token)
        assert "reg_token_abcdef123456" not in result_token
        assert REDACTED in result_token

        text_var = "docker_registry_token=reg_token_value_999"
        result_var = redact(text_var)
        assert "reg_token_value_999" not in result_var
        assert REDACTED in result_var

