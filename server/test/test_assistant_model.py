from app.models.assistant import AssistantConversation, AssistantMessage


def test_assistant_message_enum_names_match_migration() -> None:
    assert AssistantMessage.__table__.c.role.type.name == "assistant_message_role"
    assert AssistantMessage.__table__.c.status.type.name == "assistant_message_status"


def test_assistant_conversation_last_message_uses_timezone() -> None:
    assert AssistantConversation.__table__.c.last_message_at.type.timezone is True


def test_assistant_conversation_list_index_matches_query_sort() -> None:
    index_names = {index.name for index in AssistantConversation.__table__.indexes}
    assert "ix_assistant_conversation_user_last_message_id" in index_names
    assert "ix_assistant_conversation_user_updated_id" not in index_names
