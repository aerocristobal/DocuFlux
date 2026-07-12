# Use a Playwright base image which includes Node.js and browser dependencies
FROM mcr.microsoft.com/playwright:v1.61.1-jammy@sha256:7b86926fff94374389e8e1f4fdc5c76d050d4a06a7886bb537bf412b20e2b71e

WORKDIR /app

# Install any additional Node.js dependencies for mcp-server-playwright
# Assuming mcp-server-playwright is a Node.js application
COPY package.json package-lock.json ./
RUN npm install

# Copy the mcp-server-playwright application code
COPY . .

# The base image ships a non-root pwuser (uid 1000) already set up for the
# Playwright browser sandbox; run as that user instead of root.
RUN chown -R pwuser:pwuser /app
USER pwuser

# Expose the port that the MCP server will listen on
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Command to run the MCP server
CMD ["npm", "start"]
