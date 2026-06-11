/* ═══════════════════════════════════════════════════════════
   ws.js — WebSocket client with auto-reconnect
   ═══════════════════════════════════════════════════════════ */

const WS = (() => {
  let socket   = null;
  let model    = "7b";
  let handlers = {};
  let pingInterval = null;

  function connect(m) {
    model = m || model;
    if (socket) {
      // Silence the old socket — don't let its onclose trigger auto-reconnect
      socket.onclose = null;
      socket.onerror = null;
      socket.onmessage = null;
      if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)
        socket.close();
    }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${proto}://${location.host}/ws/chat?model=${model}`);

    socket.onopen = () => {
      _fire("_connected", {});
      pingInterval = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN)
          socket.send(JSON.stringify({ type: "ping" }));
      }, 25000);
    };

    socket.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      if (msg.type === "ping") { send({ type: "pong" }); return; }
      _fire(msg.type, msg);
    };

    socket.onclose = () => {
      clearInterval(pingInterval);
      _fire("_disconnected", {});
      setTimeout(() => connect(model), 2500);
    };

    socket.onerror = () => socket.close();
  }

  function send(obj) {
    if (socket && socket.readyState === WebSocket.OPEN)
      socket.send(JSON.stringify(obj));
  }

  function on(type, fn) {
    if (!handlers[type]) handlers[type] = [];
    handlers[type].push(fn);
  }

  function _fire(type, msg) {
    (handlers[type] || []).forEach(fn => fn(msg));
  }

  return { connect, send, on };
})();
