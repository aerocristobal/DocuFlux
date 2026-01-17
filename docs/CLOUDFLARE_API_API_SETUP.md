# Cloudflare API Token Setup for Certbot DNS-01 Challenge

To enable Certbot to automatically obtain and renew SSL certificates using the DNS-01 challenge with Cloudflare, you need to create a dedicated API Token with specific permissions. This document guides you through the process.

## Why DNS-01 Challenge?

The DNS-01 challenge is preferred for environments where:
- You cannot open port 80 or 443 (e.g., behind a firewall, or when using Cloudflare Tunnel which bypasses direct port exposure).
- You want to obtain wildcard certificates (e.g., `*.example.com`).

Certbot, with the `dns-cloudflare` authenticator, will interact with Cloudflare's API to create and delete DNS `TXT` records necessary to prove domain ownership.

## Step-by-Step Guide to Create Cloudflare API Token

1.  **Log in to Cloudflare**:
    Go to [Cloudflare Dashboard](https://dash.cloudflare.com/) and log in to your account.

2.  **Navigate to API Tokens**:
    -   In the top right corner, click on your profile icon.
    -   From the dropdown menu, select "My Profile".
    -   On the left-hand sidebar, click on "API Tokens".

3.  **Create a New Token**:
    -   On the "API Tokens" page, click the "Create Token" button.

4.  **Choose a Template (or Create Custom Token)**:
    -   You can either choose a suitable template if available, or, for maximum control and security, select "Create Custom Token". We recommend creating a custom token to adhere to the principle of least privilege.

5.  **Configure Token Details**:
    -   **Token Name**: Give your token a descriptive name, e.g., `Certbot-DNS-Challenge-DocuFlux`.
    -   **Permissions**: This is the most crucial step. You need to grant the following permissions:
        -   Click "Add more permissions".
        -   For the first permission:
            -   **Component**: `Zone`
            -   **Permission**: `Zone Resources`
            -   **Access**: `Read`
        -   Click "Add more permissions" again.
        -   For the second permission:
            -   **Component**: `Zone`
            -   **Permission**: `DNS`
            -   **Access**: `Edit`
    -   **Zone Resources**:
        -   Select "Include"
        -   **Specific zone**: Choose `example.com` (replace `example.com` with your actual domain name that you want Certbot to manage certificates for). If you want this token to manage multiple domains, you may need to select "All zones" (use with caution as it grants broader access). For most users, restricting to a single zone is best practice.
    -   **Client IP Address Filtering**: (Optional, but recommended) If your Certbot container will always run from a static IP address, you can restrict token usage to that IP. Otherwise, leave it unrestricted.
    -   **TTL (Time to Live)**: (Optional) You can set an expiration date for the token. For automated certificate renewal, a token with no expiration is often used, but ensure its security by other means (e.g., strong access controls on the machine it's stored on).

6.  **Continue to Summary**:
    -   Review the token's permissions and settings. Ensure they match the requirements above.

7.  **Create Token**:
    -   Click "Create Token".

8.  **Save Your Token**:
    -   **IMPORTANT**: Cloudflare will display the generated API Token **only once**. Copy it immediately and store it securely. You will use this token in the `cloudflare/credentials.ini` file.
    -   Example Token: `your_cloudflare_api_token_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

## Using the Token in `credentials.ini`

Once you have your API token, you need to place it in the `cloudflare/credentials.ini` file (which should be symlinked or copied from `cloudflare/credentials.ini.example` and then filled with your token).

**Example `cloudflare/credentials.ini` content:**

```ini
# Cloudflare API token, requires permissions:
# Zone > Zone Resources > Read
# Zone > DNS > Edit
dns_cloudflare_api_token = your_cloudflare_api_token_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**Security Note**: This `credentials.ini` file contains sensitive information.
-   Ensure it is added to your `.gitignore` to prevent accidental commits.
-   Protect it with appropriate file permissions on your host system.
-   Consider using Docker Secrets or a similar secrets management solution in production environments.

By following these steps, you will have a Cloudflare API Token ready for Certbot to automate your SSL certificate management.
