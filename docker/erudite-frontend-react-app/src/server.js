const express = require('express');
const axios = require('axios');
const cors = require('cors');
const bodyParser = require('body-parser');

const app = express();

app.use(cors({
  origin: '*',  // Be more specific in production
  methods: ['GET', 'POST', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'Authorization']
}));

app.use(bodyParser.json());

app.options('/proxy-chat-messages', cors());  // Enable pre-flight request for POST request

app.get('/proxy-chat-messages', (req, res) => {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive'
  });

  // Send a test event every 5 seconds
  const intervalId = setInterval(() => {
    res.write(`data: ${JSON.stringify({message: 'Test message'})}\n\n`);
  }, 5000);

  req.on('close', () => {
    clearInterval(intervalId);
  });
});

app.listen(3001, () => console.log('Proxy server running on port 3001'));