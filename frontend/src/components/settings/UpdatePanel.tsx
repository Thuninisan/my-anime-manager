import { useEffect, useState } from 'react';

export default function UpdatePanel() {
  const [version, setVersion] = useState<string>('...');

  useEffect(() => {
    fetch('/api/version')
      .then(async response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then(data => setVersion(data.version ?? 'Unknown'))
      .catch(() => setVersion('Unavailable'));
  }, []);

  return (
    <section className="bg-card rounded-xl border border-border/30 p-6 space-y-4">
      <h3 className="font-semibold text-foreground">Application Version</h3>
      <p className="font-mono text-lg">{version}</p>
      <p className="text-sm text-muted-foreground">
        To update a Docker installation, pull the latest source and run
        <code className="mx-1">docker compose up -d --build</code>
        on the host.
      </p>
    </section>
  );
}
