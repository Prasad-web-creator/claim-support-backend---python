# Implementation file inventory

This inventory compares the local implementation branch with the original root checkout. The backend coverage sources and Flutter policy and prescription sources already existed in nested checkouts. The root ignore change admits these dependencies for clean-checkout builds.

## Modified

- `.gitignore`
- `backend-python/Dockerfile`
- `backend-python/app/api/router.py`
- `backend-python/app/api/routes/analysis.py`
- `backend-python/app/api/routes/app_config.py`
- `backend-python/app/api/routes/auth.py`
- `backend-python/app/api/routes/policy_custom.py`
- `backend-python/app/api/routes/upload.py`
- `backend-python/app/core/config.py`
- `backend-python/app/core/database.py`
- `backend-python/app/core/exceptions.py`
- `backend-python/app/core/logging.py`
- `backend-python/app/core/security.py`
- `backend-python/app/middleware/auth.py`
- `backend-python/app/middleware/upload_validation.py`
- `backend-python/app/models/otp.py`
- `backend-python/app/models/prescription.py`
- `backend-python/app/models/stored_file.py`
- `backend-python/app/models/user.py`
- `backend-python/app/schemas/auth.py`
- `backend-python/app/services/analysis_interactive_orchestrator.py`
- `backend-python/app/services/analysis_orchestrator.py`
- `backend-python/app/services/cleanup_service.py`
- `backend-python/app/services/crud_service.py`
- `backend-python/app/services/extraction/document_extraction.py`
- `backend-python/app/services/extraction/metadata_extraction.py`
- `backend-python/app/services/extraction/policy_continuity_extraction.py`
- `backend-python/app/services/extraction/policy_extraction.py`
- `backend-python/app/services/extraction/prescription_extraction.py`
- `backend-python/app/services/multi_policy_orchestrator.py`
- `backend-python/app/services/ocr/ocr_engine.py`
- `backend-python/app/services/otp_service.py`
- `backend-python/app/services/pdf/pdf_extractor.py`
- `backend-python/app/services/storage/file_upload_service.py`
- `backend-python/app/services/storage/gridfs_provider.py`
- `backend-python/app/services/ttl_service.py`
- `backend-python/app/utils/file_validators.py`
- `backend-python/main.py`
- `backend-python/requirements-dev.txt`
- `backend-python/requirements.txt`
- `backend-python/scripts/email_check.py`
- `backend-python/tests/conftest.py`
- `backend-python/tests/test_ai_only_policy_inputs.py`
- `backend-python/tests/test_multi_policy_analysis.py`
- `backend-python/tests/test_otp_auth.py`
- `backend-python/tests/test_shared_file_delete.py`
- `mobile-app/lib/core/config/app_config_provider.dart`
- `mobile-app/lib/core/network/api_client.dart`
- `mobile-app/lib/core/network/auth_interceptor.dart`
- `mobile-app/lib/features/analysis/presentation/controllers/analysis_controller.dart`
- `mobile-app/lib/features/analysis/presentation/controllers/multi_analysis_controller.dart`
- `mobile-app/lib/features/analysis/presentation/screens/analysis_screen.dart`
- `mobile-app/lib/features/analysis/presentation/widgets/clarification_dialog.dart`
- `mobile-app/lib/features/authentication/data/models/user.dart`
- `mobile-app/lib/features/authentication/presentation/screens/login_screen.dart`
- `mobile-app/lib/features/authentication/presentation/screens/register_screen.dart`
- `mobile-app/lib/features/authentication/presentation/widgets/otp_method_selector.dart`
- `mobile-app/lib/features/profile/presentation/screens/settings_screen.dart`
- `mobile-app/lib/features/upload/presentation/screens/upload_policy_screen.dart`
- `mobile-app/lib/features/upload/presentation/screens/upload_prescription_screen.dart`
- `mobile-app/lib/main.dart`
- `mobile-app/pubspec.lock`
- `mobile-app/pubspec.yaml`

## Created

- `.gitlab-ci.yml`
- `backend-python/.env.example`
- `backend-python/app/core/telemetry.py`
- `backend-python/app/services/coverage/__init__.py`
- `backend-python/app/services/coverage/business_rules.py`
- `backend-python/app/services/coverage/cover_duration.py`
- `backend-python/app/services/coverage/coverage_analysis.py`
- `backend-python/app/services/coverage/coverage_prompt.py`
- `backend-python/app/services/coverage/gemini_triage.py`
- `backend-python/app/services/coverage/policy_clause_search.py`
- `backend-python/app/services/coverage/policy_comparison.py`
- `backend-python/app/services/coverage/policy_continuity.py`
- `backend-python/app/services/coverage/policy_evidence.py`
- `backend-python/app/services/coverage/report_generator.py`
- `backend-python/app/services/coverage/verification.py`
- `backend-python/app/services/extraction/labelled_fields.py`
- `backend-python/app/services/google_sign_in.py`
- `backend-python/app/services/maintenance.py`
- `backend-python/app/services/pdf/pdf_access.py`
- `backend-python/app/services/rule_engine/adjudication.py`
- `backend-python/app/services/rule_engine/catalog.py`
- `backend-python/app/services/rule_engine/coverage_rules.json`
- `backend-python/app/services/storage/file_lease.py`
- `backend-python/app/services/storage/pdf_optimization.py`
- `backend-python/app/services/storage/storage_monitor.py`
- `backend-python/docs/coverage-rules.md`
- `backend-python/docs/deployment-and-verification.md`
- `backend-python/docs/environment.md`
- `backend-python/docs/implementation-files.md`
- `backend-python/docs/service-setup.md`
- `backend-python/docs/storage-operations.md`
- `backend-python/scripts/container_smoke.py`
- `backend-python/scripts/draft_coverage_rules.py`
- `backend-python/scripts/mongo_backup.py`
- `backend-python/scripts/retire_retention_indexes.py`
- `backend-python/tests/test_coverage_data_boundaries.py`
- `backend-python/tests/test_coverage_gold_set.py`
- `backend-python/tests/test_gridfs_storage.py`
- `mobile-app/lib/core/services/error_reporting.dart`
- `mobile-app/lib/features/authentication/data/google_identity.dart`
- `mobile-app/lib/features/policies/data/models/policy.dart`
- `mobile-app/lib/features/policies/data/repositories/policy_repository.dart`
- `mobile-app/lib/features/policies/presentation/controllers/policy_controller.dart`
- `mobile-app/lib/features/policies/presentation/screens/policy_list_screen.dart`
- `mobile-app/lib/features/prescriptions/data/models/prescription.dart`
- `mobile-app/lib/features/prescriptions/data/repositories/prescription_repository.dart`
- `mobile-app/lib/features/prescriptions/presentation/controllers/prescription_controller.dart`
- `mobile-app/lib/features/prescriptions/presentation/screens/prescription_list_screen.dart`
- `mobile-app/test/app_config_test.dart`
- `render.yaml`

## Removed

- Retired backend routes and services for SMS OTP, policy retrieval, the Insurance Knowledge Base, expert review, dataset tuning, and prescription text review.
- Unused Flutter screens for My Benefits, Coverage Insights, and Activity Logs.

The local backend environment was updated to use the new upload, policy and OTP limits. The environment file is ignored by Git.
