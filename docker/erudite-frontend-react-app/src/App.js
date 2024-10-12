import React, { useState, useEffect } from 'react';

function App() {
  const [connectionStatus, setConnectionStatus] = useState('Disconnected');
  const [messages, setMessages] = useState([]);

  useEffect(() => {
    const eventSource = new EventSource('http://localhost:3001/proxy-chat-messages');

    eventSource.onopen = () => {
      console.log('EventSource connected');
      setConnectionStatus('Connected');
    };

    eventSource.onmessage = (event) => {
      console.log('Received message:', event.data);
      setMessages(prev => [...prev, JSON.parse(event.data).message]);
    };

    eventSource.onerror = (error) => {
      console.error('EventSource failed:', error);
      setConnectionStatus('Error: Connection failed');
    };

    return () => {
      eventSource.close();
    };
  }, []);

  return (
    <div>
      <h1>Dify Chat</h1>
      <p>Connection Status: {connectionStatus}</p>
      <ul>
        {messages.map((msg, index) => (
          <li key={index}>{msg}</li>
        ))}
      </ul>
    </div>
  );
}

export default App;