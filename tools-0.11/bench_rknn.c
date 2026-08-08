/*
 * Minimal C benchmark using RKNN C API (same as production modeld).
 * Compiles on-device, links against librknnrt.so.
 * 
 * Usage: ./bench_rknn <model.rknn> [num_runs]
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/stat.h>
#include "rknn_api.h"

static long long gettime_us() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

static unsigned char* read_file(const char* path, size_t* size) {
    FILE* f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "cannot open %s\n", path); return NULL; }
    fseek(f, 0, SEEK_END);
    *size = ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char* data = (unsigned char*)malloc(*size);
    fread(data, 1, *size, f);
    fclose(f);
    return data;
}

static float* create_dummy_input(rknn_tensor_attr* attr) {
    size_t elem_count = 1;
    for (int i = 0; i < attr->n_dims; i++) elem_count *= attr->dims[i];
    size_t byte_size = elem_count * sizeof(float);  // assume float32
    float* data = (float*)malloc(byte_size);
    // Fill with dummy values (128.0 mid-gray for images, 0 for others)
    float fill = 128.0f;
    if (attr->n_dims >= 2 && attr->dims[attr->n_dims - 1] <= 8) fill = 0.0f;  // small tensors = non-image
    for (size_t i = 0; i < elem_count; i++) data[i] = fill;
    return data;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <model.rknn> [num_runs=20]\n", argv[0]);
        return 1;
    }
    const char* model_path = argv[1];
    int num_runs = (argc >= 3) ? atoi(argv[2]) : 20;

    // Load model
    size_t model_len;
    unsigned char* model_data = read_file(model_path, &model_len);
    if (!model_data) return 1;
    printf("Model: %s (%zu bytes)\n", model_path, model_len);

    // Init RKNN
    rknn_context ctx;
    int ret = rknn_init(&ctx, model_data, model_len, 0, NULL);
    if (ret < 0) { fprintf(stderr, "rknn_init failed: %d\n", ret); return 1; }

    // Set core 2 (same as production modeld)
    rknn_set_core_mask(ctx, RKNN_NPU_CORE_2);

    // Query input/output count
    rknn_input_output_num io_num;
    rknn_query(ctx, RKNN_QUERY_IN_OUT_NUM, &io_num, sizeof(io_num));
    printf("Inputs: %d, Outputs: %d\n", io_num.n_input, io_num.n_output);

    // Query input attrs
    rknn_tensor_attr input_attrs[16];
    memset(input_attrs, 0, sizeof(input_attrs));
    for (uint32_t i = 0; i < io_num.n_input; i++) {
        input_attrs[i].index = i;
        rknn_query(ctx, RKNN_QUERY_INPUT_ATTR, &input_attrs[i], sizeof(rknn_tensor_attr));
        printf("  input[%d]: %s, dims=[", i, input_attrs[i].name);
        for (int j = 0; j < input_attrs[i].n_dims; j++) printf("%d%s", input_attrs[i].dims[j], j < input_attrs[i].n_dims-1 ? "," : "");
        printf("], type=%d, size=%d\n", input_attrs[i].type, input_attrs[i].size);
    }

    // Create dummy inputs (float32, pass_through=0 so RKNN handles conversion)
    rknn_input inputs[16];
    memset(inputs, 0, sizeof(inputs));
    for (uint32_t i = 0; i < io_num.n_input; i++) {
        size_t elem_count = 1;
        for (int j = 0; j < input_attrs[i].n_dims; j++) elem_count *= input_attrs[i].dims[j];
        inputs[i].index = i;
        inputs[i].buf = create_dummy_input(&input_attrs[i]);
        inputs[i].size = elem_count * sizeof(float);
        inputs[i].pass_through = 0;
        inputs[i].type = RKNN_TENSOR_FLOAT32;
        inputs[i].fmt = RKNN_TENSOR_NHWC;
    }

    // Query output attrs
    rknn_tensor_attr output_attrs[16];
    memset(output_attrs, 0, sizeof(output_attrs));
    for (uint32_t i = 0; i < io_num.n_output; i++) {
        output_attrs[i].index = i;
        rknn_query(ctx, RKNN_QUERY_NATIVE_OUTPUT_ATTR, &output_attrs[i], sizeof(rknn_tensor_attr));
    }

    // Allocate output buffers
    float* outputs[16];
    for (uint32_t i = 0; i < io_num.n_output; i++) {
        outputs[i] = (float*)malloc(output_attrs[i].n_elems * sizeof(float));
    }

    // Warmup (3 runs)
    printf("Warmup (3 runs)...\n");
    for (int w = 0; w < 3; w++) {
        rknn_inputs_set(ctx, io_num.n_input, inputs);
        rknn_output rknn_outs[16];
        memset(rknn_outs, 0, sizeof(rknn_outs));
        for (uint32_t i = 0; i < io_num.n_output; i++) {
            rknn_outs[i].want_float = 1;
            rknn_outs[i].is_prealloc = 1;
            rknn_outs[i].index = i;
            rknn_outs[i].buf = outputs[i];
            rknn_outs[i].size = output_attrs[i].n_elems * sizeof(float);
        }
        rknn_run(ctx, NULL);
        rknn_outputs_get(ctx, io_num.n_output, rknn_outs);
        rknn_outputs_release(ctx, io_num.n_output, rknn_outs);
    }

    // Check output validity
    printf("Output[0]: n_elems=%d, first 5: ", output_attrs[0].n_elems);
    for (int i = 0; i < 5 && i < (int)output_attrs[0].n_elems; i++) printf("%.3f ", outputs[0][i]);
    printf("...\n");

    // Benchmark
    printf("Benchmark (%d runs)...\n", num_runs);
    double times[100];
    for (int r = 0; r < num_runs && r < 100; r++) {
        rknn_inputs_set(ctx, io_num.n_input, inputs);
        rknn_output rknn_outs[16];
        memset(rknn_outs, 0, sizeof(rknn_outs));
        for (uint32_t i = 0; i < io_num.n_output; i++) {
            rknn_outs[i].want_float = 1;
            rknn_outs[i].is_prealloc = 1;
            rknn_outs[i].index = i;
            rknn_outs[i].buf = outputs[i];
            rknn_outs[i].size = output_attrs[i].n_elems * sizeof(float);
        }
        long long t0 = gettime_us();
        rknn_run(ctx, NULL);
        rknn_outputs_get(ctx, io_num.n_output, rknn_outs);
        long long t1 = gettime_us();
        times[r] = (double)(t1 - t0) / 1000.0;
        rknn_outputs_release(ctx, io_num.n_output, rknn_outs);
    }

    // Stats
    double sum = 0, min_t = 999999, max_t = 0;
    for (int r = 0; r < num_runs && r < 100; r++) {
        sum += times[r];
        if (times[r] < min_t) min_t = times[r];
        if (times[r] > max_t) max_t = times[r];
    }
    double mean = sum / num_runs;
    // median
    for (int i = 0; i < num_runs && i < 100; i++)
        for (int j = i+1; j < num_runs && j < 100; j++)
            if (times[j] < times[i]) { double tmp = times[i]; times[i] = times[j]; times[j] = tmp; }
    double median = times[num_runs/2];

    printf("\n========================================\n");
    printf("RESULTS: %s\n", model_path);
    printf("========================================\n");
    printf("Mean:   %.1f ms  (%.1f Hz)\n", mean, 1000.0/mean);
    printf("Median: %.1f ms\n", median);
    printf("Min:    %.1f ms  Max: %.1f ms\n", min_t, max_t);
    printf("========================================\n");

    // Cleanup
    for (uint32_t i = 0; i < io_num.n_input; i++) free(inputs[i].buf);
    for (uint32_t i = 0; i < io_num.n_output; i++) free(outputs[i]);
    rknn_destroy(ctx);
    free(model_data);
    return 0;
}
