// Polling for a queued background job (Steam and PlayStation syncs).

export const POLL_MS = 5000;
export const ACTIVE_JOB = new Set(['queued', 'in_progress', 'deferred']);
export const isActiveJob = (status) => ACTIVE_JOB.has(status);

/**
 * One poll chain. `fetchJob(id)` reads the job; while it is queued, in
 * progress or deferred `onActive(job)` runs and the next read is scheduled.
 * Otherwise `onSettled(job, opts)` runs. A thrown error goes to `onError`.
 * `begin()` runs at the start of each read and returns a `stale()` check, so a
 * result that arrives after the page changed is dropped. `opts` reaches the
 * callbacks of the first read only, as the scheduled reads carry none.
 */
export function createJobPoller({ fetchJob, onActive, onSettled, onError, begin = () => () => false, intervalMs = POLL_MS }) {
  let timer = null;
  const stop = () => { clearTimeout(timer); timer = null; };
  async function poll(jobId, opts) {
    stop();
    const stale = begin();
    try {
      const job = await fetchJob(jobId);
      if (stale()) return;
      if (isActiveJob(job.status)) {
        onActive(job, opts);
        timer = setTimeout(() => void poll(jobId), intervalMs);
        return;
      }
      await onSettled(job, opts);
    } catch (error) { if (!stale()) onError(error, opts); }
  }
  return { poll, stop };
}
