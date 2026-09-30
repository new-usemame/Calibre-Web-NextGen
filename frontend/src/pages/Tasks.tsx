import { ListChecks, X } from 'lucide-react';
import { useTasks, useCancelTask } from '../lib/queries';
import { SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import { taskText } from '../lib/taskText';
import styles from './Tasks.module.css';

export function Tasks() {
  const { data, isLoading, error } = useTasks();
  const cancel = useCancelTask();
  const t = useT();

  if (isLoading) return <SpinnerCentered size={40} />;
  if (error || !data) {
    return (
      <main className={styles.container}>
        <EmptyState message={error instanceof Error ? error.message : t('Could not load tasks.')} />
      </main>
    );
  }

  return (
    <main className={styles.container}>
      <div className={styles.header}>
        <ListChecks size={22} className={styles.headerIcon} />
        <h1 className={styles.title}>{t('Tasks')}</h1>
        <span className={styles.count}>{data.items.length}</span>
      </div>

      {data.items.length === 0 ? (
        <EmptyState message={t('No tasks running.')} />
      ) : (
        <table className={styles.table}>
          <thead>
            <tr>
              <th>{t('Task')}</th>
              <th>{t('User')}</th>
              <th>{t('Status')}</th>
              <th>{t('Progress')}</th>
              <th>{t('Run time')}</th>
              <th aria-label={t('Cancel')} />
            </tr>
          </thead>
          <tbody>
            {data.items.map((task) => (
              <tr key={String(task.task_id)}>
                <td className={styles.taskMsg}>{taskText(task.taskMessage)}</td>
                <td>{taskText(task.user)}</td>
                <td>{task.status ?? '—'}</td>
                <td>{task.progress}</td>
                <td>{task.runtime ?? '—'}</td>
                <td>
                  {task.is_cancellable && (
                    <button className={styles.cancelBtn}
                      onClick={() => cancel.mutate(task.task_id)}
                      disabled={cancel.isPending}
                      aria-label={t('Cancel {task}', { task: taskText(task.taskMessage) })}>
                      <X size={15} />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
