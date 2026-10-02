# Frappe / ERPNext Integration with On-Premise Active Directory

This document outlines the steps to integrate an open-source self-hosted Frappe/ERPNext instance with a corporate On-Premise Windows Active Directory (AD). 

## 1. Prerequisites (Why LDAP Settings Might Be Missing)

If you do not see **LDAP Settings** in your Frappe instance (under Home > Integrations), it is because Frappe dynamically hides the LDAP module if the required Python library is not installed on the server.

You must install the `ldap3` dependency directly into the Frappe bench Python environment.

### Installation Steps
1. SSH into the server hosting your Frappe instance.
2. Navigate to your frappe bench directory (e.g., `/home/frappe/frappe-bench`).
3. Run the following command to install the required library into Frappe's virtual environment:
   ```bash
   ./env/bin/pip install ldap3
   ```
4. Restart your Frappe services (e.g., `bench restart` or restart via supervisor).

---

## 2. Configuring LDAP / Active Directory in Frappe

Once the `ldap3` library is installed and services are restarted, you can configure the AD connection from the web interface.

1. Log into your Frappe / ERPNext desk as a System Manager / Administrator.
2. Using the global search bar (Awesomebar) at the top, type and select **LDAP Settings** (or navigate to **Home > Integrations > LDAP Settings**).
3. Check the **Enabled** box.
4. Input your Active Directory network and credential parameters:

### AD Connection Parameters
* **LDAP Server URL**: `ldaps://<YOUR_DC_IP_OR_HOSTNAME>:636`
  *(Note: Use `ldap://...:389` if you are not using SSL, though LDAPS is highly recommended for security).*
* **Base Distinguished Name (DN)**: The DN of the service account used to query AD.
  *(Example: `CN=svc-ai-agent,OU=ServiceAccounts,DC=nationalfinance,DC=local`)*
* **Password for Base DN**: The password for the service account.

### User Search Parameters
* **Organizational Unit (OU) of Users**: The base search path for your users.
  *(Example: `DC=nationalfinance,DC=local` or a specific OU like `OU=Users,DC=nationalfinance,DC=local`)*
* **LDAP Search String**: For standard Windows Active Directory, use:
  ```text
  sAMAccountName={0}
  ```
  *(Alternatively, use `userPrincipalName={0}` if you want users to log in with their full email addresses).*

### Field Mapping (Optional but Recommended)
Map the AD attributes to the corresponding Frappe User fields:
* **Email Field**: `mail`
* **Username Field**: `sAMAccountName`
* **First Name Field**: `givenName`

---

## 3. Testing and Rollout

1. Click **Save** in the top right corner.
2. Log out of Frappe.
3. On the main login screen, you will now see a new **Login with LDAP** button below the standard username/password fields.
4. IT staff and employees can now log in using their standard Windows Active Directory credentials. 

*(Upon their first successful login via LDAP, Frappe will automatically create a User profile for them in the system and assign the default roles you specified in the LDAP Settings).*
