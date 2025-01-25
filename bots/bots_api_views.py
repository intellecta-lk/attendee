from django.shortcuts import render, get_object_or_404
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import Bot, BotEventTypes, BotEventManager, Recording, RecordingTypes, TranscriptionTypes, TranscriptionProviders, Utterance, MediaBlob, BotMediaRequest, BotMediaRequestMediaTypes
from .serializers import CreateBotSerializer, BotSerializer, TranscriptUtteranceSerializer, RecordingSerializer
from .authentication import ApiKeyAuthentication
from .tasks import run_bot
import redis
import json
import os
from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiParameter, OpenApiExample

TokenHeaderParameter = [
    OpenApiParameter(
        name="Authorization",
        type=str,
        location=OpenApiParameter.HEADER,
        description="API key for authentication",
        required=True,
        default="Token YOUR_API_KEY_HERE"
    ),
    OpenApiParameter(
        name="Content-Type",
        type=str,
        location=OpenApiParameter.HEADER,
        description="Should always be application/json",
        required=True,
        default="application/json"
    )
]

LeavingBotExample = OpenApiExample(
                            'Leaving Bot',
                            value={
                                'id': 'bot_weIAju4OXNZkDTpZ', 
                                'meeting_url': 'https://zoom.us/j/123?pwd=456', 
                                'state': 'leaving',
                                'events': [
                                    {
                                        'type': 'join_requested',
                                        'created_at': '2024-01-18T12:34:56Z'
                                    },
                                    {
                                        'type': 'joined_meeting',
                                        'created_at': '2024-01-18T12:35:00Z'
                                    },
                                    {
                                        'type': 'leave_requested',
                                        'created_at': '2024-01-18T13:34:56Z'
                                    }
                                ],
                                'transcription_state': 'in_progress',
                                'recording_state': 'in_progress'
                            },
                            description='Example response when requesting a bot to leave'
                        )

NewlyCreatedBotExample = OpenApiExample(
            'New bot',
            value={
                'id': 'bot_weIAju4OXNZkDTpZ', 
                'meeting_url': 'https://zoom.us/j/123?pwd=456', 
                'state': 'joining',
                'events': [
                    {
                        'type': 'join_requested',
                        'created_at': '2024-01-18T12:34:56Z'
                    }
                ],
                'transcription_state': 'not_started',
                'recording_state': 'not_started'
            },
            description='Example response when creating a new bot'
        )

@extend_schema(exclude=True)
class NotFoundView(APIView):    
    def get(self, request, *args, **kwargs):
        return self.handle_request(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.handle_request(request, *args, **kwargs)

    def put(self, request, *args, **kwargs):
        return self.handle_request(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        return self.handle_request(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        return self.handle_request(request, *args, **kwargs)

    def handle_request(self, request, *args, **kwargs):
        return Response(
            {'error': 'Not found'},
            status=status.HTTP_404_NOT_FOUND
        )

def send_sync_command(bot, command='sync'):
    redis_url = os.getenv('REDIS_URL') + ("?ssl_cert_reqs=none" if os.getenv('DISABLE_REDIS_SSL') else "")
    redis_client = redis.from_url(redis_url)
    channel = f"bot_{bot.id}"
    message = {
        'command': command
    }
    redis_client.publish(channel, json.dumps(message))

class BotCreateView(APIView):
    authentication_classes = [ApiKeyAuthentication]

    @extend_schema(
        operation_id='Create Bot',
        summary='Create a new bot',
        description='After being created,the bot will attempt to join the specified meeting.',
        request=CreateBotSerializer,
        responses={
            201: OpenApiResponse(response=BotSerializer, description='Bot created successfully', examples=[NewlyCreatedBotExample]),
            400: OpenApiResponse(description='Invalid input')
        },
        parameters=TokenHeaderParameter, 
        tags=['Bots'],
    )
    def post(self, request):
        serializer = CreateBotSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        # Access the bot through the api key
        project = request.auth.project
        
        meeting_url = serializer.validated_data['meeting_url']
        bot_name = serializer.validated_data['bot_name']
        transcription_settings = serializer.validated_data['transcription_settings']
        settings = {
            'transcription_settings': transcription_settings
        }
        bot = Bot.objects.create(
            project=project,
            meeting_url=meeting_url,
            name=bot_name,
            settings=settings,
        )

        Recording.objects.create(
            bot=bot,
            recording_type=RecordingTypes.AUDIO_AND_VIDEO,
            transcription_type=TranscriptionTypes.NON_REALTIME,
            transcription_provider=TranscriptionProviders.DEEPGRAM,
            is_default_recording=True
        )
        
        # Try to transition the state from READY to JOINING
        BotEventManager.create_event(bot, BotEventTypes.JOIN_REQUESTED)

        # Launch the Celery task after successful creation
        run_bot.delay(bot.id)
        
        return Response(
            BotSerializer(bot).data,
            status=status.HTTP_201_CREATED
        )

class OutputAudioView(APIView):
    authentication_classes = [ApiKeyAuthentication]
    
    @extend_schema(
        operation_id='Output Audio',
        summary='Output audio',
        description='Causes the bot to output audio in the meeting.',
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'type': {'type': 'string', 'enum': [ct[0] for ct in MediaBlob.VALID_AUDIO_CONTENT_TYPES]},
                    'data': {'type': 'string', 'format': 'binary', 'description': 'Base64 encoded audio data'}
                },
                'required': ['type', 'data']
            }
        },
        responses={
            200: OpenApiResponse(description='Audio request created successfully'),
            400: OpenApiResponse(description='Invalid input'),
            404: OpenApiResponse(description='Bot not found')
        },
        parameters=TokenHeaderParameter,
        tags=['Bots'],
    )
    def post(self, request, object_id):
        try:
            # Validate request data
            if 'type' not in request.data or 'data' not in request.data:
                return Response(
                    {'error': 'Both type and data are required'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            content_type = request.data['type']
            if content_type not in [ct[0] for ct in MediaBlob.VALID_AUDIO_CONTENT_TYPES]:
                return Response(
                    {'error': 'Invalid audio content type'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            try:
                # Decode base64 data
                import base64
                audio_data = base64.b64decode(request.data['data'])
            except Exception:
                return Response(
                    {'error': 'Invalid base64 encoded data'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Get the bot
            bot = Bot.objects.get(object_id=object_id, project=request.auth.project)
            if not BotEventManager.is_state_that_can_play_media(bot.state):
                return Response(
                    {'error': 'Bot is not in a state that can play media'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Create or get existing MediaBlob
            media_blob = MediaBlob.get_or_create_from_blob(
                project=request.auth.project,
                blob=audio_data,
                content_type=content_type
            )
            
            # Create BotMediaRequest
            BotMediaRequest.objects.create(
                bot=bot,
                media_blob=media_blob,
                media_type=BotMediaRequestMediaTypes.AUDIO
            )
            
            # Send sync command
            send_sync_command(bot, 'sync_media_requests')
            
            return Response(status=status.HTTP_200_OK)
            
        except Bot.DoesNotExist:
            return Response(
                {'error': 'Bot not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
    
class OutputImageView(APIView):
    authentication_classes = [ApiKeyAuthentication]
    
    @extend_schema(
        operation_id='Output Image',
        summary='Output image',
        description='Causes the bot to output an image in the meeting.',
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'type': {'type': 'string', 'enum': [ct[0] for ct in MediaBlob.VALID_IMAGE_CONTENT_TYPES]},
                    'data': {'type': 'string', 'format': 'binary', 'description': 'Base64 encoded image data'}
                },
                'required': ['type', 'data']
            }
        },
        responses={
            200: OpenApiResponse(description='Image request created successfully'),
            400: OpenApiResponse(description='Invalid input'),
            404: OpenApiResponse(description='Bot not found')
        },
        parameters=TokenHeaderParameter,
        tags=['Bots'],
    )
    def post(self, request, object_id):
        try:
            # Validate request data
            if 'type' not in request.data or 'data' not in request.data:
                return Response(
                    {'error': 'Both type and data are required'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            content_type = request.data['type']
            if content_type not in [ct[0] for ct in MediaBlob.VALID_IMAGE_CONTENT_TYPES]:
                return Response(
                    {'error': 'Invalid image content type'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            try:
                # Decode base64 data
                import base64
                image_data = base64.b64decode(request.data['data'])
            except Exception:
                return Response(
                    {'error': 'Invalid base64 encoded data'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Get the bot
            bot = Bot.objects.get(object_id=object_id, project=request.auth.project)
            if not BotEventManager.is_state_that_can_play_media(bot.state):
                return Response(
                    {'error': 'Bot is not in a state that can play media'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Create or get existing MediaBlob
            media_blob = MediaBlob.get_or_create_from_blob(
                project=request.auth.project,
                blob=image_data,
                content_type=content_type
            )
            
            # Create BotMediaRequest
            BotMediaRequest.objects.create(
                bot=bot,
                media_blob=media_blob,
                media_type=BotMediaRequestMediaTypes.IMAGE
            )
            
            # Send sync command
            send_sync_command(bot, 'sync_media_requests')
            
            return Response(status=status.HTTP_200_OK)
            
        except Bot.DoesNotExist:
            return Response(
                {'error': 'Bot not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )

class BotLeaveView(APIView):
    authentication_classes = [ApiKeyAuthentication]
        
    @extend_schema(
        operation_id='Leave Meeting',
        summary='Leave a meeting',
        description='Causes the bot to leave the meeting.',
        responses={
            200: OpenApiResponse(
                    response=BotSerializer,
                    description='Successfully requested to leave meeting',
                    examples=[LeavingBotExample]
                ),
            404: OpenApiResponse(description='Bot not found')
        },
        parameters=TokenHeaderParameter,
        tags=['Bots'],
    )
    def post(self, request, object_id):
        try:
            bot = Bot.objects.get(object_id=object_id, project=request.auth.project)
            
            BotEventManager.create_event(bot, BotEventTypes.LEAVE_REQUESTED)

            send_sync_command(bot)
            
            return Response(
                BotSerializer(bot).data,
                status=status.HTTP_200_OK
            )
            
        except Bot.DoesNotExist:
            return Response(
                {'error': 'Bot not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )

class RecordingView(APIView):
    authentication_classes = [ApiKeyAuthentication]

    @extend_schema(
        operation_id='Get Bot Recording',
        summary='Get the recording for a bot',
        description='Returns a short-lived S3 URL for the recording of the bot.',
        responses={
            200: OpenApiResponse(response=RecordingSerializer, description='Short-lived S3 URL for the recording')
        },
        parameters=TokenHeaderParameter,
        tags=['Bots'],
    )
    def get(self, request, object_id):
        try:
            bot = Bot.objects.get(object_id=object_id, project=request.auth.project)

            recording = Recording.objects.filter(bot=bot, is_default_recording=True).first()
            if not recording:
                return Response(
                    {'error': 'No recording found for bot'}, 
                    status=status.HTTP_404_NOT_FOUND
                )
            
            recording_file = recording.file
            if not recording_file:
                return Response(
                    {'error': 'No recording file found for bot'}, 
                    status=status.HTTP_404_NOT_FOUND
                )
            
            return Response(RecordingSerializer(recording).data)
            
        except Bot.DoesNotExist:
            return Response(
                {'error': 'Bot not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )

class TranscriptView(APIView):
    authentication_classes = [ApiKeyAuthentication]
    
    @extend_schema(
        operation_id='Get Bot Transcript',
        summary='Get the transcript for a bot',
        description='If the meeting is still in progress, this returns the transcript so far.',
        responses={
            200: OpenApiResponse(response=TranscriptUtteranceSerializer(many=True), description='List of transcribed utterances'),
            404: OpenApiResponse(description='Bot not found')
        },
        parameters=TokenHeaderParameter,
        tags=['Bots'],
    )
    def get(self, request, object_id):
        try:
            bot = Bot.objects.get(object_id=object_id, project=request.auth.project)
            
            recording = Recording.objects.filter(bot=bot, is_default_recording=True).first()
            if not recording:
                return Response(
                    {'error': 'No recording found for bot'}, 
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Get all utterances with transcriptions, sorted by timeline
            utterances = Utterance.objects.select_related('participant').filter(
                recording=recording,
                transcription__isnull=False
            ).order_by('timestamp_ms')
            
            # Format the response, skipping empty transcriptions
            transcript_data = [
                {
                    'speaker_name': utterance.participant.full_name,
                    'speaker_uuid': utterance.participant.uuid,
                    'speaker_user_uuid': utterance.participant.user_uuid,
                    'timestamp_ms': utterance.timestamp_ms,
                    'duration_ms': utterance.duration_ms,
                    'transcription': utterance.transcription
                }
                for utterance in utterances     
                if utterance.transcription.get('transcript', '')
            ]
            
            serializer = TranscriptUtteranceSerializer(transcript_data, many=True)
            return Response(serializer.data)
            
        except Bot.DoesNotExist:
            return Response(
                {'error': 'Bot not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )

class BotDetailView(APIView):
    authentication_classes = [ApiKeyAuthentication]
    
    @extend_schema(
        operation_id='Get Bot',
        summary='Get the details for a bot',
        responses={
            200: OpenApiResponse(response=BotSerializer, description='Bot details', examples=[NewlyCreatedBotExample]),
            404: OpenApiResponse(description='Bot not found')
        },
        parameters=TokenHeaderParameter,
        tags=['Bots'],
    )        
    def get(self, request, object_id):
        try:
            bot = Bot.objects.get(object_id=object_id, project=request.auth.project)
            return Response(BotSerializer(bot).data)
            
        except Bot.DoesNotExist:
            return Response(
                {'error': 'Bot not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
