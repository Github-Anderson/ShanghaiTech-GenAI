"""Public, credential-free contract and historical verification boundaries.

Verification describes recorded evidence, not a live upstream health check.
"""
from .attachments import MAX_FILE_BYTES, MAX_IMAGE_BYTES, MAX_FILES, FILE_TTL_SECONDS, DOCUMENT_MIMES, IMAGE_MIMES
from .protocol import MAX_HISTORY_MESSAGES
from .conversations import TTL_SECONDS, MAX_CONVERSATIONS
from .tool_bridge import MAX_ARGUMENT_JSON_DECODES, TAGGED_ARGUMENT_TYPES

PROTOCOL_VERSION = '1.1'


def describe_capabilities(models):
    entries = []
    for model, profile in models.items():
        upstream = profile.get('upstream_model', model)
        entries.append({
            'id': model, 'name': profile.get('display_name', model),
            'search': {
                'supported': profile.get('supports_search', False),
                'default': profile.get('net_go', False),
                'verification': 'request_and_stream_observed' if upstream == 'qwen-instruct' else 'not_verified',
            },
            'thinking': {
                'supported': bool(profile.get('supports_thinking') and profile.get('enable_thinking')),
                'default': profile.get('thinking', False),
                'verification': 'request_observed' if upstream == 'Kimi-k3' else 'not_verified',
            },
            'attachments': {
                'images': {'supported': profile.get('supports_images', False),
                           'verification': 'gateway_roundtrip_passed_20261030' if upstream == 'deepseek-pro' else 'not_verified',
                           'max_bytes': MAX_IMAGE_BYTES, 'max_bytes_exclusive': True, 'mime_types': list(IMAGE_MIMES)},
                'pdf': {'supported': profile.get('supports_files', False),
                        'verification': 'gateway_roundtrip_passed_dsh_ui_pending' if upstream == 'deepseek-pro' else 'not_verified'},
                'documents': {'supported': profile.get('supports_files', False), 'extensions': list(DOCUMENT_MIMES),
                              'max_bytes': MAX_FILE_BYTES, 'max_bytes_exclusive': True, 'multiple': True,
                              'scanned_documents': False, 'reference_field': 'fileIds',
                              'format_basis': 'successful_synthetic_uploads_20261030',
                              'unconfirmed_formats': ['doc', 'xls', 'xlsx', 'ppt', 'pptx', 'html', 'json', 'xml'],
                              'upstream_rejected_formats': ['md', 'rtf']},
            },
            'max_output_tokens': profile.get('max_tokens', 16384),
        })
    return {
        'object': 'gateway.capabilities', 'protocol_version': PROTOCOL_VERSION,
        'options': {'request_field': 'shanghaitech', 'search_scope': 'upstream_site',
                    'sources_phase': 'after_chat_completion', 'sources_response_field': 'shanghaitech.sources',
                    'independent_options': True},
        'models': entries,
        'files': {'max_bytes': MAX_FILE_BYTES, 'ttl_seconds': FILE_TTL_SECONDS,
                  'attachment_contract_version': 2,
                  'max_image_bytes': MAX_IMAGE_BYTES, 'max_document_bytes': MAX_FILE_BYTES,
                  'size_boundary': 'strictly_less_than',
                  'size_limit_basis': 'captured_frontend_raw_file_check',
                  'upstream_near_limit_uploads': 'may_be_rejected_by_server_multipart_or_model_content_limits',
                  'max_parsed_response_bytes': 64 * 1024 * 1024,
                  'max_active': MAX_FILES, 'max_images_per_message': MAX_FILES,
                  'max_documents_per_conversation': MAX_FILES, 'count_limit_scope': 'local_gateway_capacity',
                  'max_pdfs_per_conversation': MAX_FILES, 'mixed_pdf_images': True,
                  'mixed_pdf_representation': 'website_file_ids_and_images',
                  'upload_batch_transport': 'one_multipart_request_per_file_same_conversation_id',
                  'duplicate_policy': 'credential_model_group_mime_sha256',
                  'expired_reference_policy': 'reupload_from_dsh_source',
                  'document_extensions': list(DOCUMENT_MIMES), 'image_mime_types': list(IMAGE_MIMES),
                  'model_bound': True, 'credential_bound': True, 'persistent': False,
                  'delete_scope': 'gateway_reference_only'},
        'tools': {'mode': 'text_bridge', 'execution': 'client', 'max_parallel_calls': 8,
                  'argument_encodings': ['object', 'json_string'], 'exact_registered_names': True,
                  'max_argument_json_decodes': MAX_ARGUMENT_JSON_DECODES,
                  'tagged_argument_types': list(TAGGED_ARGUMENT_TYPES),
                  'final_text_literal_line_whitespace': True,
                  'strict_schema_validation': True, 'automatic_model_retries': True,
                  'retry_policy': {'model': 'deepseek-pro', 'error': 'upstream_empty_stream',
                                   'max_retries': 1, 'requires_no_output': True},
                  'image_business_retry_policy': {'model': 'deepseek-pro', 'site_code': 500,
                      'max_retries': 1, 'shared_with_empty_retry': True, 'requires_no_output': True,
                      'requires_image': True, 'excludes': ['authentication', 'quota', 'input'],
                      'reuploads_attachments': False},
                  'empty_pdf_read_dispatch': 'explicit_named_tool_choice_only',
                  'verification': 'partial_read_and_command_roundtrips'},
        'limits': {'max_history_messages': MAX_HISTORY_MESSAGES},
        'usage': {'available': True, 'availability': 'when_reported_by_upstream',
                  'stream_opt_in': 'stream_options.include_usage', 'site_counters_separate': True},
        'conversations': {'request_field': 'conversation_id', 'persistent': False,
                          'ttl_seconds': TTL_SECONDS, 'max_active': MAX_CONVERSATIONS,
                          'same_group_serialized': True, 'history': 'caller_supplied'},
        'verification': {'basis': 'recorded_tests', 'live_probe': False,
                         'all_combinations_verified': False},
    }
