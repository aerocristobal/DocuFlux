# Use a Playwright base image which includes Node.js and browser dependencies
FROM mcr.microsoft.com/playwright:v1.40.0-jammy

WORKDIR /app

# Install any additional Node.js dependencies for mcp-server-playwright
# Assuming mcp-server-playwright is a Node.js application
COPY package.json package-lock.json ./
RUN npm install

# Copy the mcp-server-playwright application code
COPY . .

# Expose the port that the MCP server will listen on
EXPOSE 8080

# Command to run the MCP server
CMD ["npm", "start"]
