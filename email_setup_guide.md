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

## Option 2: SendGrid (Recommended for Production)

1. Sign up at https://sendgrid.com (free tier: 100 emails/day)
2. Create an API key:
   - Settings → API Keys → Create API Key
   - Give it "Mail Send" permissions
3. Use in `.env`:
   ```
   EMAIL_PROVIDER=sendgrid
   SENDGRID_API_KEY=your-api-key
   SENDER_EMAIL=noreply@yourdomain.com
   ```

## Option 3: Outlook/Hotmail

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

## Option 4: Custom Domain Email

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
- For production use, consider using an email API service like SendGrid