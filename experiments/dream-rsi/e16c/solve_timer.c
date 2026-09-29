/* E16c compute-only timer (claims audit Q13, review round). Loaded with LD_PRELOAD into an unmodified solver
 * binary that speaks the SimpleTES wire format (fread from stdin, fwrite to stdout). It records the monotonic
 * time when the last fread on stdin returns, when the first fwrite on stdout starts and when the last fwrite on
 * stdout returns, and writes "<first-write window> <last-write window>" in nanoseconds (both measured from the
 * last read) to the file named by $RSI_SOLVE_TIMER_OUT at exit. The first-write window is the compute time of
 * a program that writes its whole path at the end; a program that streams one column per lambda needs the
 * last-write window, which also includes writing the output into the pipe. The same shim is used for every
 * compiled program, so input parsing and output writing are excluded identically. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static long long t_read = -1, t_write = -1, t_wend = -1;
static long long now_ns(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec * 1000000000LL + ts.tv_nsec; }

size_t fread(void *ptr, size_t size, size_t n, FILE *s) {
    static size_t (*real)(void *, size_t, size_t, FILE *) = 0;
    if (!real) real = dlsym(RTLD_NEXT, "fread");
    size_t r = real(ptr, size, n, s);
    if (s == stdin && t_write < 0) t_read = now_ns();
    return r;
}
size_t fwrite(const void *ptr, size_t size, size_t n, FILE *s) {
    static size_t (*real)(const void *, size_t, size_t, FILE *) = 0;
    if (!real) real = dlsym(RTLD_NEXT, "fwrite");
    if (s == stdout && t_write < 0) t_write = now_ns();
    size_t r = real(ptr, size, n, s);
    if (s == stdout) t_wend = now_ns();
    return r;
}
__attribute__((destructor)) static void report(void) {
    const char *f = getenv("RSI_SOLVE_TIMER_OUT");
    if (!f) return;
    FILE *o = fopen(f, "w");
    if (!o) return;
    fprintf(o, "%lld %lld\n", (t_read >= 0 && t_write >= 0) ? (t_write - t_read) : -1LL,
            (t_read >= 0 && t_wend >= 0) ? (t_wend - t_read) : -1LL);
    fclose(o);
}
