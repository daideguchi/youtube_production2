interface ActivityLogItem {
  title: string;
  description?: string;
  timestamp?: string;
}

interface ActivityLogProps {
  items: ActivityLogItem[];
}

export function ActivityLog({ items }: ActivityLogProps) {
  if (!items.length) {
    return (
      <div className="activity-log">
        <h2>最近の操作</h2>
        <p className="muted">まだ操作履歴がありません。</p>
      </div>
    );
  }

  return (
    <div className="activity-log">
      <h2>最近の操作</h2>
      <ul>
        {items.map((item) => (
          <li key={item.title + item.timestamp}>
            <strong>{item.title}</strong>
            {item.description && <span> — {item.description}</span>}
            {item.timestamp && <div className="activity-log__time">{item.timestamp}</div>}
          </li>
        ))}
      </ul>
    </div>
  );
}
