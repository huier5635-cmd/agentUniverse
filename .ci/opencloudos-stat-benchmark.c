// SPDX-License-Identifier: GPL-2.0-only
#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

struct sample {
	uint64_t cpu[10];
	uint64_t uptime;
	int have_cpu, have_uptime;
};

static uint64_t nanoseconds(clockid_t clock)
{
	struct timespec now;

	if (clock_gettime(clock, &now)) {
		perror("clock_gettime");
		exit(2);
	}
	return (uint64_t)now.tv_sec * 1000000000 + now.tv_nsec;
}

static void transfer(int fd, void *data, size_t length, int writing)
{
	char *p = data;

	while (length) {
		ssize_t done = writing ? write(fd, p, length) : read(fd, p, length);

		if (done < 0 && errno == EINTR)
			continue;
		if (done <= 0) {
			perror("pipe transfer");
			exit(2);
		}
		p += done;
		length -= done;
	}
}

static void pin_cpu(int cpu)
{
	cpu_set_t set;

	CPU_ZERO(&set);
	CPU_SET(cpu, &set);
	if (sched_setaffinity(0, sizeof(set), &set)) {
		perror("sched_setaffinity");
		exit(2);
	}
}

static struct sample sample(const char *path)
{
	struct sample result = {};
	char *line = NULL;
	size_t capacity = 0;
	FILE *file = fopen(path, "r");

	if (!file) {
		perror(path);
		exit(2);
	}
	while (getline(&line, &capacity, file) >= 0) {
		if (!strncmp(line, "cpu0 ", 5)) {
			unsigned long long values[10];
			int count, i;

			count = sscanf(line + 5,
				"%llu %llu %llu %llu %llu %llu %llu %llu %llu %llu",
				&values[0], &values[1], &values[2], &values[3],
				&values[4], &values[5], &values[6], &values[7],
				&values[8], &values[9]);
			if (count != 10)
				exit(2);
			for (i = 0; i < 10; i++)
				result.cpu[i] = values[i];
			result.have_cpu = 1;
		} else if (!strncmp(line, "uptime ", 7)) {
			unsigned long long value;

			if (sscanf(line + 7, "%llu", &value) != 1)
				exit(2);
			result.uptime = value;
			result.have_uptime = 1;
		}
	}
	if (ferror(file) || !result.have_cpu)
		exit(2);
	free(line);
	fclose(file);
	return result;
}

static void burst(int signal_number)
{
	struct timespec now;
	uint64_t start, current;

	(void)signal_number;
	if (clock_gettime(CLOCK_MONOTONIC, &now))
		_exit(2);
	start = (uint64_t)now.tv_sec * 1000000000 + now.tv_nsec;
	do {
		if (clock_gettime(CLOCK_MONOTONIC, &now))
			_exit(2);
		current = (uint64_t)now.tv_sec * 1000000000 + now.tv_nsec;
	} while (current - start < 50000);
}

static void workload(int mode, int phase, int command, int result_fd)
{
	struct sigaction action = { .sa_handler = burst };
	struct sigevent event = { .sigev_notify = SIGEV_SIGNAL,
				 .sigev_signo = SIGUSR1 };
	struct itimerspec period = {};
	sigset_t blocked;
	timer_t timer = 0;
	uint64_t start, cpu_start, cpu_used;
	char ready = 1;

	pin_cpu(0);
	if (mode == 2) {
		sigemptyset(&action.sa_mask);
		if (sigaction(SIGUSR1, &action, NULL) ||
		    timer_create(CLOCK_MONOTONIC, &event, &timer))
			exit(2);
	}
	transfer(result_fd, &ready, 1, 1);
	transfer(command, &ready, 1, 0);
	cpu_start = nanoseconds(CLOCK_PROCESS_CPUTIME_ID);
	start = nanoseconds(CLOCK_MONOTONIC);
	if (mode == 2) {
		uint64_t first = (start / 1000000 + 2) * 1000000 + phase * 62500;

		period.it_value.tv_sec = first / 1000000000;
		period.it_value.tv_nsec = first % 1000000000;
		period.it_interval.tv_nsec = 1000000;
		if (timer_settime(timer, TIMER_ABSTIME, &period, NULL))
			exit(2);
	}
	if (mode == 0) {
		struct timespec delay = { .tv_sec = 1 };

		while (nanosleep(&delay, &delay) && errno == EINTR)
			;
	} else {
		while (nanoseconds(CLOCK_MONOTONIC) - start < 1000000000) {
			if (mode == 2)
				pause();
		}
	}
	if (mode == 2) {
		sigemptyset(&blocked);
		sigaddset(&blocked, SIGUSR1);
		if (sigprocmask(SIG_BLOCK, &blocked, NULL))
			exit(2);
		timer_delete(timer);
	}
	cpu_used = nanoseconds(CLOCK_PROCESS_CPUTIME_ID) - cpu_start;
	transfer(result_fd, &cpu_used, sizeof(cpu_used), 1);
	_exit(0);
}

static void measure(int mode, int phase, const char *path)
{
	struct sample before, after;
	uint64_t start, elapsed, cpu_used, total = 0, idle;
	int command[2], result_pipe[2], status, i;
	char ready;
	pid_t child;

	if (pipe(command) || pipe(result_pipe))
		exit(2);
	child = fork();
	if (child < 0)
		exit(2);
	if (!child) {
		close(command[1]);
		close(result_pipe[0]);
		workload(mode, phase, command[0], result_pipe[1]);
	}
	close(command[0]);
	close(result_pipe[1]);
	transfer(result_pipe[0], &ready, 1, 0);
	before = sample(path);
	start = nanoseconds(CLOCK_MONOTONIC);
	transfer(command[1], &ready, 1, 1);
	transfer(result_pipe[0], &cpu_used, sizeof(cpu_used), 0);
	after = sample(path);
	elapsed = nanoseconds(CLOCK_MONOTONIC) - start;
	close(command[1]);
	close(result_pipe[0]);
	if (waitpid(child, &status, 0) != child || !WIFEXITED(status) ||
	    WEXITSTATUS(status))
		exit(2);
	for (i = 0; i < 8; i++) {
		if (after.cpu[i] < before.cpu[i])
			exit(2);
		total += after.cpu[i] - before.cpu[i];
	}
	idle = after.cpu[3] - before.cpu[3];
	if (!total || idle > total)
		exit(2);
	printf("OC_MEASUREMENT {\"mode\":%d,\"phase\":%d,\"reference\":%.8f,"
	       "\"legacy\":%.8f,\"cpu_ns\":%" PRIu64 ",\"elapsed_ns\":%" PRIu64,
	       mode, phase, (double)cpu_used / elapsed,
	       (double)(total - idle) / total, cpu_used, elapsed);
	if (before.have_uptime && after.have_uptime) {
		uint64_t ticks = after.uptime - before.uptime;

		if (!ticks)
			exit(2);
		printf(",\"normalized\":%.8f,\"uptime_ticks\":%" PRIu64
		       ",\"idle_ticks\":%" PRIu64,
		       idle >= ticks ? 0.0 : (double)(ticks - idle) / ticks,
		       ticks, idle);
	}
	puts("}");
	fflush(stdout);
}

static void read_cost(const char *path)
{
	int repeat, i;

	for (repeat = 0; repeat < 3; repeat++) {
		uint64_t start = nanoseconds(CLOCK_PROCESS_CPUTIME_ID);

		for (i = 0; i < 2000; i++)
			sample(path);
		printf("OC_READ_COST {\"path\":\"%s\",\"repeat\":%d,"
		       "\"cpu_ns_per_read\":%.2f}\n", path, repeat,
		       (double)(nanoseconds(CLOCK_PROCESS_CPUTIME_ID) - start) / 2000);
	}
}

int main(void)
{
	const char *path = access("/proc/stat_no_full_hz", F_OK) ?
		"/proc/stat" : "/proc/stat_no_full_hz";
	int phase;

	pin_cpu(1);
	measure(0, 0, path);
	measure(1, 0, path);
	for (phase = 0; phase < 16; phase++)
		measure(2, phase, path);
	read_cost("/proc/stat");
	if (!strcmp(path, "/proc/stat_no_full_hz"))
		read_cost(path);
	return 0;
}
