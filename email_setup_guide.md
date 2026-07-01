# Email Setup Guide

## Option 1: Gmail (Recommended for Personal Use)

1. Create a new Gmail account (or use existing)
2. Go to Google Account settings
3. Enable 2-factor authentication
4. Generate app password:
   - Visit https://myaccount.google.com/apppasswords
   - Select "Mail" and your device
   - Copy the 16-character password
5. Use in `.env`:
   ```
   SMTP_SERVER=smtp.gmail.com
   SMTP_PORT=587
   SENDER_EMAIL=your-email@gmail.com
   SENDER_PASSWORD=your-16-char-app-password
   ```

> **Note:** SkylightSync currently supports SMTP only. Use one of the SMTP
> options below (Gmail, Outlook, or a custom domain).

## Option 2: Outlook/Hotmail

1. Use your Microsoft account
2. Enable 2-factor authentication
3. Create app password at https://account.microsoft.com/security
4. Use in `.env`:
   ```
   SMTP_SERVER=smtp-mail.outlook.com
   SMTP_PORT=587
   SENDER_EMAIL=your-email@outlook.com
   SENDER_PASSWORD=your-app-password
   ```

## Option 3: Custom Domain Email

Use your hosting provider's SMTP settings. Common providers:

### Namecheap Private Email
```
SMTP_SERVER=mail.privateemail.com
SMTP_PORT=587
SENDER_EMAIL=noreply@yourdomain.com
SENDER_PASSWORD=your-password
```

### Google Workspace
```
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SENDER_EMAIL=noreply@yourdomain.com
SENDER_PASSWORD=your-app-password
```

## Security Notes

- Never commit credentials to git
- Use app-specific passwords, not your main password
- Consider creating a dedicated email account for this service