# Use an official Node runtime as the parent image
FROM node:18-alpine

# Set the working directory in the container
WORKDIR /app

# Copy package.json and package-lock.json
COPY package*.json ./

# Copy the proxy server file
COPY src/server.js .

# Install dependencies and serve globally
RUN npm install express axios cors body-parser
RUN npm install && npm install -g serve

# Copy the rest of your app's source code
COPY . .

# Build the app
RUN npm run build

# Expose the ports the app runs on
EXPOSE 9998 3001

# Start both the proxy server and the React app
CMD ["sh", "-c", "node src/server.js & serve -s build -l 9998"]