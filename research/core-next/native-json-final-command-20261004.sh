PYTHONPATH=src .venv/bin/python -m pytest -q \
 tests/primary/test_kernel_native_json.py \
 tests/primary/test_kernel_compression.py tests/primary/test_kernel_compression_review.py \
 tests/primary/test_kernel_threads.py tests/primary/test_kernel_threads_review.py \
 tests/primary/test_kernel_memory.py tests/primary/test_kernel_memory_review.py tests/primary/test_kernel_memory_transfer.py tests/primary/test_kernel_memory_activation.py tests/primary/test_kernel_memory_activation_review.py \
 tests/primary/test_kernel_models.py \
 tests/primary/test_kernel_results.py tests/primary/test_kernel_results_review.py tests/primary/test_kernel_result_inputs.py \
 tests/primary/test_kernel_datasets.py tests/primary/test_kernel_datasets_review.py \
 tests/primary/test_kernel_record_plugin.py tests/primary/test_kernel_record_plugin_review.py \
 tests/primary/test_kernel_actors.py tests/primary/test_kernel_actor_fields.py tests/primary/test_kernel_actor_fields_review.py \
 tests/primary/test_kernel_workspace.py tests/primary/test_kernel_workspace_review.py \
 tests/primary/test_kernel_observation.py tests/primary/test_kernel_observation_http.py tests/primary/test_kernel_observation_domains.py \
 tests/primary/test_kernel_analysis_export.py tests/primary/test_public_core_api.py \
 tests/experiments/test_core_next_compression_probe.py tests/experiments/test_core_next_cold_datasets_probe.py tests/experiments/test_core_next_large_root_probe.py
