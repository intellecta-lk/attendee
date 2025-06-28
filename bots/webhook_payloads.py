from bots.serializers import ChatMessageSerializer, ParticipantEventSerializer


def chat_message_webhook_payload(chat_message):
    return ChatMessageSerializer(chat_message).data


def utterance_webhook_payload(utterance):
    return {
        "speaker_name": utterance.participant.full_name,
        "speaker_uuid": utterance.participant.uuid,
        "speaker_user_uuid": utterance.participant.user_uuid,
        "timestamp_ms": utterance.timestamp_ms,
        "duration_ms": utterance.duration_ms,
        "transcription": {"transcript": utterance.transcription.get("transcript")} if utterance.transcription else None,
    }


def participant_event_webhook_payload(participant_event):
    return ParticipantEventSerializer(participant_event).data
