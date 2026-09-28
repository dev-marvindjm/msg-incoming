import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_auth_registration_and_login(client: AsyncClient):
    # 1. Register new user
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={"email": "new_trader@quant.com", "password": "SecurePassword123!"},
    )
    assert reg_res.status_code == 201
    data = reg_res.json()["data"]
    assert data["email"] == "new_trader@quant.com"

    # 2. Login to get token
    login_res = await client.post(
        "/api/v1/auth/token",
        data={"username": "new_trader@quant.com", "password": "SecurePassword123!"},
    )
    assert login_res.status_code == 200
    token_data = login_res.json()
    assert "access_token" in token_data
    token = token_data["access_token"]

    # 3. Read profile
    me_res = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_res.status_code == 200
    assert me_res.json()["data"]["email"] == "new_trader@quant.com"


@pytest.mark.asyncio
async def test_sender_rules_crud(client: AsyncClient, auth_headers_user_a: dict):
    # 1. Create Rule
    create_res = await client.post(
        "/api/v1/rules/senders",
        headers=auth_headers_user_a,
        json={
            "platform": "TELEGRAM",
            "entity_id": "-10011223344",
            "entity_name": "alpha_signals",
            "policy": "ALLOW",
            "is_active": True,
            "forward_to_brokers": True,
        },
    )
    assert create_res.status_code == 201
    rule_data = create_res.json()["data"]
    rule_id = rule_data["id"]
    assert rule_data["policy"] == "ALLOW"
    assert rule_data["entity_id"] == "-10011223344"

    # 2. List Rules
    list_res = await client.get("/api/v1/rules/senders", headers=auth_headers_user_a)
    assert list_res.status_code == 200
    rules = list_res.json()["data"]
    assert len(rules) == 1
    assert rules[0]["id"] == rule_id

    # 3. Toggle Rule
    toggle_res = await client.put(f"/api/v1/rules/senders/{rule_id}/toggle", headers=auth_headers_user_a)
    assert toggle_res.status_code == 200
    assert toggle_res.json()["data"]["is_active"] is False

    # 4. Delete Rule
    del_res = await client.delete(f"/api/v1/rules/senders/{rule_id}", headers=auth_headers_user_a)
    assert del_res.status_code == 200
    assert del_res.json()["data"]["success"] is True

    # 5. Verify deletion
    list_after_res = await client.get("/api/v1/rules/senders", headers=auth_headers_user_a)
    assert len(list_after_res.json()["data"]) == 0


@pytest.mark.asyncio
async def test_global_filter_config_api(client: AsyncClient, auth_headers_user_a: dict):
    # 1. Get initial config
    get_res = await client.get("/api/v1/rules/config?platform=TELEGRAM", headers=auth_headers_user_a)
    assert get_res.status_code == 200
    configs = get_res.json()["data"]
    assert len(configs) >= 1
    assert configs[0]["allow_any_sender"] is False

    # 2. Update config dynamically
    put_res = await client.put(
        "/api/v1/rules/config",
        headers=auth_headers_user_a,
        json={"platform": "TELEGRAM", "allow_any_sender": True, "strict_whitelist": False},
    )
    assert put_res.status_code == 200
    updated_cfg = put_res.json()["data"]
    assert updated_cfg["allow_any_sender"] is True


@pytest.mark.asyncio
async def test_webhook_message_ingestion_and_history(client: AsyncClient, auth_headers_user_a: dict):
    # 1. Configure allow_any_sender for THREADS
    await client.put(
        "/api/v1/rules/config",
        headers=auth_headers_user_a,
        json={"platform": "THREADS", "allow_any_sender": True},
    )

    # 2. Ingest message via webhook
    webhook_res = await client.post(
        "/api/v1/messages/webhook",
        headers=auth_headers_user_a,
        json={
            "platform": "THREADS",
            "external_message_id": "thread_post_1001",
            "sender_id": "threads_trader_vip",
            "sender_username": "threads_trader_vip",
            "raw_text": "BUY EURUSD at market",
            "metadata_json": {"tags": ["forex", "scalping"]},
        },
    )
    assert webhook_res.status_code == 201
    result_data = webhook_res.json()["data"]
    assert result_data["message"]["delivery_status"] == "EMITTED"
    assert result_data["message"]["external_message_id"] == "thread_post_1001"

    # 3. Query History
    history_res = await client.get("/api/v1/messages/history", headers=auth_headers_user_a)
    assert history_res.status_code == 200
    history = history_res.json()["data"]
    assert history["total"] == 1
    assert history["items"][0]["external_message_id"] == "thread_post_1001"


@pytest.mark.asyncio
async def test_multi_user_api_isolation(
    client: AsyncClient,
    auth_headers_user_a: dict,
    auth_headers_user_b: dict,
):
    # User A creates a rule
    res_a = await client.post(
        "/api/v1/rules/senders",
        headers=auth_headers_user_a,
        json={
            "platform": "TELEGRAM",
            "entity_id": "exclusive_channel_a",
            "policy": "ALLOW",
        },
    )
    rule_a_id = res_a.json()["data"]["id"]

    # User B lists rules - should NOT see User A's rule
    list_b = await client.get("/api/v1/rules/senders", headers=auth_headers_user_b)
    rules_b = list_b.json()["data"]
    assert not any(r["id"] == rule_a_id for r in rules_b)

    # User B attempts to delete User A's rule - should return 404
    del_res = await client.delete(f"/api/v1/rules/senders/{rule_a_id}", headers=auth_headers_user_b)
    assert del_res.status_code == 404
