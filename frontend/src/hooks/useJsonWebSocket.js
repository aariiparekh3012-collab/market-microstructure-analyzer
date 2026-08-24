import { useEffect, useRef, useState } from 'react';

export function useJsonWebSocket(url, { historyLimit = 300 } = {}) {
  const [latest, setLatest] = useState(null);
  const [history, setHistory] = useState([]);
  const [status, setStatus] = useState('connecting');
  const wsRef = useRef(null);
  const backoffRef = useRef(500);

  useEffect(() => {
    let cancelled = false;
    let retryTimer;
    
    setLatest(null);
    setHistory([]);
    setStatus('connecting');
    
    backoffRef.current = 500;

    function connect() {
      if (cancelled) return;
      const ws = new WebSocket(url);
      wsRef.current = ws;
      setStatus('connecting');

      ws.onopen = () => { setStatus('live'); backoffRef.current = 500; };
      ws.onclose = () => {
        setStatus('disconnected');
        if (!cancelled) {
          setTimeout(connect, backoffRef.current);
          backoffRef.current = Math.min(backoffRef.current * 2, 8000);
        }
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (evt) => {
        try {
          const data = JSON.parse(evt.data);
          setLatest(data);
          setHistory((h) => {
            const next = [...h, data];
            return next.length > historyLimit ? next.slice(-historyLimit) : next;
          });
        } catch (e) {
          console.warn('bad ws payload', e);
        }
      };
    }

    connect();
    return () => { cancelled = true; wsRef.current?.close(); };
  }, [url, historyLimit]);

  return { latest, history, status };
}
