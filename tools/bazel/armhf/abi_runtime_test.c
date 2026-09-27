#define _POSIX_C_SOURCE 200809L

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

static void report_error(const char *operation) {
    fprintf(stderr, "%s: %s\n", operation, strerror(errno));
}

int main(void) {
    int result = 1;
    int fd = -1;
    int directory_created = 0;
    int file_created = 0;
    DIR *entries = NULL;
    char directory[PATH_MAX];
    char path[PATH_MAX];
    struct stat info;
    const char marker = 'x';
    char actual = '\0';
    size_t entry_count = 0;
    int found_file = 0;

    if (sizeof(off_t) != 8 || sizeof(time_t) != 8) {
        fprintf(stderr, "expected 64-bit off_t and time_t, got %zu and %zu bytes\n",
                sizeof(off_t), sizeof(time_t));
        return 1;
    }

    const char *test_tmpdir = getenv("TEST_TMPDIR");
    if (test_tmpdir == NULL) {
        test_tmpdir = "/tmp";
    }
    int length = snprintf(directory, sizeof(directory), "%s/armhf-abi-XXXXXX", test_tmpdir);
    if (length < 0 || (size_t)length >= sizeof(directory)) {
        fprintf(stderr, "temporary directory path is too long\n");
        return 1;
    }
    if (mkdtemp(directory) == NULL) {
        report_error("mkdtemp");
        return 1;
    }
    directory_created = 1;
    length = snprintf(path, sizeof(path), "%s/sparse-file", directory);
    if (length < 0 || (size_t)length >= sizeof(path)) {
        fprintf(stderr, "temporary file path is too long\n");
        goto cleanup;
    }

    fd = open(path, O_CREAT | O_EXCL | O_RDWR | O_CLOEXEC, 0600);
    if (fd < 0) {
        report_error("open");
        goto cleanup;
    }
    file_created = 1;

    const off_t large_size = (off_t)(INT64_C(3) * 1024 * 1024 * 1024 + 17);
    if (ftruncate(fd, large_size) != 0) {
        report_error("ftruncate beyond 2 GiB");
        goto cleanup;
    }
    if (pwrite(fd, &marker, 1, large_size - 1) != 1) {
        report_error("pwrite beyond 2 GiB");
        goto cleanup;
    }
    if (pread(fd, &actual, 1, large_size - 1) != 1) {
        report_error("pread beyond 2 GiB");
        goto cleanup;
    }
    if (actual != marker || lseek(fd, 0, SEEK_END) != large_size) {
        fprintf(stderr, "large-file data or offset did not round trip\n");
        goto cleanup;
    }
    if (fstat(fd, &info) != 0) {
        report_error("fstat large file");
        goto cleanup;
    }
    if (info.st_size != large_size) {
        fprintf(stderr, "large-file size mismatch: got %jd, expected %jd\n",
                (intmax_t)info.st_size, (intmax_t)large_size);
        goto cleanup;
    }
    const intmax_t allocated_bytes = (intmax_t)info.st_blocks * 512;
    if (allocated_bytes > 1024 * 1024) {
        fprintf(stderr, "sparse file allocated too much data: %jd bytes\n", allocated_bytes);
        goto cleanup;
    }

    const time_t future = (time_t)INT64_C(2208988800); /* 2040-01-01 UTC */
    const struct timespec timestamps[2] = {{future, 0}, {future + 123, 0}};
    if (futimens(fd, timestamps) != 0) {
        report_error("futimens after 2038");
        goto cleanup;
    }
    if (fstat(fd, &info) != 0) {
        report_error("fstat after 2038");
        goto cleanup;
    }
    if (info.st_atim.tv_sec != timestamps[0].tv_sec ||
        info.st_mtim.tv_sec != timestamps[1].tv_sec) {
        fprintf(stderr, "post-2038 timestamps did not round trip: atime=%jd mtime=%jd\n",
                (intmax_t)info.st_atim.tv_sec, (intmax_t)info.st_mtim.tv_sec);
        goto cleanup;
    }

    entries = opendir(directory);
    if (entries == NULL) {
        report_error("opendir");
        goto cleanup;
    }
    for (;;) {
        errno = 0;
        struct dirent *entry = readdir(entries);
        if (entry == NULL) {
            if (errno != 0) {
                report_error("readdir");
                goto cleanup;
            }
            break;
        }
        ++entry_count;
        if (strcmp(entry->d_name, "sparse-file") == 0) {
            found_file = 1;
        }
    }
    if (!found_file) {
        fprintf(stderr, "readdir did not return the test file\n");
        goto cleanup;
    }

    printf("ARMHF ABI: off_t=%zu time_t=%zu size=%jd allocated=%jd mtime=%jd entries=%zu\n",
           sizeof(off_t), sizeof(time_t), (intmax_t)info.st_size, allocated_bytes,
           (intmax_t)info.st_mtim.tv_sec, entry_count);
    result = 0;

cleanup:
    if (entries != NULL && closedir(entries) != 0) {
        report_error("closedir");
        result = 1;
    }
    if (fd >= 0 && close(fd) != 0) {
        report_error("close");
        result = 1;
    }
    if (file_created && unlink(path) != 0) {
        report_error("unlink");
        result = 1;
    }
    if (directory_created && rmdir(directory) != 0) {
        report_error("rmdir");
        result = 1;
    }
    return result;
}
