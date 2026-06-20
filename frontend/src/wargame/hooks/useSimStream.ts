
/**
 * Opens a WebSocket connection to /ws/simulations/{id},
 * parses incoming frames, feeds events into the Zustand store,
 * and exposes a control function for pause/resume/abort.
 */

import { useEffect, useRef, useCallback } from 'react';
import type { WsFrame, WsControlFrame, ControlAction } from '@/lib/types/ws-frame';
import { useSimStore } from '@/lib/store/simStore';

// Default: derive wss://<same-origin>/api/wargame from window.location so
// the embedded swarm subapp's WebSocket answers directly. Override via
// VITE_SWARM_WS_URL to point at a separately-deployed swarm.
function _defaultWsUrl(): string {
  if (typeof window === 'undefined') return 'ws://localhost:8000/api/wargame';
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${scheme}//${window.location.host}/api/wargame`;
}

const WS_URL =
  (import.meta.env.VITE_SWARM_WS_URL as string | undefined)?.replace(/\/$/, '') ?? _defaultWsUrl();

/** Maximum number of reconnect attempts before giving up. */
const MAX_RECONNECTS = 5;
const RECONNECT_DELAY_MS = 2000;

export interface UseSimStreamResult {
  /** Send a control command to the server. */
  control: (action: ControlAction) => void;
}

let _seqCounter = 0;

export function useSimStream(simId: string | null): UseSimStreamResult {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectCount = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Latched when we receive a non-recoverable server error. Prevents the
  // reconnect loop from spamming /ws/simulations/<missing-id> forever.
  const giveUpRef = useRef(false);

  const addEvent = useSimStore((s) => s.addEvent);
  const setSimStatus = useSimStore((s) => s.setSimStatus);
  const setCurrentTurn = useSimStore((s) => s.setCurrentTurn);
  const setWorldSnapshot = useSimStore((s) => s.setWorldSnapshot);

  // Stable control sender — reads wsRef so it never needs to be recreated
  const control = useCallback(
    (action: ControlAction) => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN || !simId) {
        return;
      }
      const frame: WsControlFrame = {
        frame_type: 'control',
        sim_id: simId,
        seq: ++_seqCounter,
        ts: new Date().toISOString(),
        payload: { frame_type: 'control', action },
      };
      wsRef.current.send(JSON.stringify(frame));
    },
    [simId],
  );

  useEffect(() => {
    if (!simId) return;

    function connect() {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }

      const url = `${WS_URL}/ws/simulations/${simId}`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectCount.current = 0;
      };

      ws.onmessage = (ev: MessageEvent<string>) => {
        let frame: WsFrame;
        try {
          frame = JSON.parse(ev.data) as WsFrame;
        } catch {
          console.warn('[useSimStream] Failed to parse WS frame', ev.data);
          return;
        }

        switch (frame.frame_type) {
          case 'connected': {
            const p = frame.payload as import('@/lib/types/ws-frame').ConnectedPayload;
            setSimStatus(p.status as import('@/lib/types/scenario').SimulationStatus);
            setCurrentTurn(p.current_turn);
            break;
          }
          case 'turn_start': {
            const p = frame.payload as import('@/lib/types/ws-frame').TurnStartPayload;
            setCurrentTurn(p.turn);
            setWorldSnapshot(p.world_state);
            break;
          }
          case 'sim_event': {
            const p = frame.payload as import('@/lib/types/ws-frame').SimEventPayload;
            addEvent(p.event);
            break;
          }
          case 'turn_end': {
            const p = frame.payload as import('@/lib/types/ws-frame').TurnEndPayload;
            setCurrentTurn(p.turn);
            break;
          }
          case 'sim_complete': {
            const p = frame.payload as import('@/lib/types/ws-frame').SimCompletePayload;
            setSimStatus(p.status as import('@/lib/types/scenario').SimulationStatus);
            break;
          }
          case 'heartbeat': {
            const p = frame.payload as import('@/lib/types/ws-frame').HeartbeatPayload;
            setSimStatus(p.status as import('@/lib/types/scenario').SimulationStatus);
            break;
          }
          case 'error': {
            const p = frame.payload as import('@/lib/types/ws-frame').ErrorPayload;
            console.error('[useSimStream] Server error:', p.code, p.message);
            if (!p.recoverable) {
              setSimStatus('error');
              // Permanently give up on this sim — non-recoverable errors
              // like NOT_FOUND shouldn't spam reconnect attempts.
              giveUpRef.current = true;
              if (ws) {
                ws.onclose = null;
                ws.close();
              }
            }
            break;
          }
        }
      };

      ws.onerror = () => {
        // Errors are followed by onclose; let onclose handle reconnect
      };

      ws.onclose = () => {
        if (giveUpRef.current) return;
        if (reconnectCount.current < MAX_RECONNECTS) {
          reconnectCount.current += 1;
          reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY_MS);
        } else {
          console.error('[useSimStream] Max reconnect attempts reached');
          setSimStatus('error');
        }
      };
    }

    connect();

    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (wsRef.current) {
        wsRef.current.onclose = null; // prevent reconnect on intentional close
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [simId, addEvent, setSimStatus, setCurrentTurn, setWorldSnapshot]);

  return { control };
}
