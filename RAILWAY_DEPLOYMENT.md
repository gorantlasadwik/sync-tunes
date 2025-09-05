# Railway Deployment Guide for Sync Tunes

This guide will help you deploy the Sync Tunes application to Railway with Spotify integration.

## Prerequisites

1. **Railway Account**: Sign up at [railway.app](https://railway.app)
2. **Spotify Developer Account**: Create at [developer.spotify.com](https://developer.spotify.com)
3. **Google Cloud Console**: For YouTube API access

## Step 1: Set Up Spotify App

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new app
3. Note down your `Client ID` and `Client Secret`
4. **Important**: Add your Railway domain to Redirect URIs:
   - `https://your-app-name.railway.app/spotify_callback`
   - You'll get the exact URL after deploying to Railway

## Step 2: Set Up YouTube API

1. Go to [Google Cloud Console](https://console.developers.google.com/)
2. Create a new project or select existing one
3. Enable YouTube Data API v3
4. Create OAuth 2.0 credentials
5. Add your Railway domain to Authorized redirect URIs:
   - `https://your-app-name.railway.app/youtube_callback`

## Step 3: Deploy to Railway

### Option A: Deploy from GitHub

1. Push your code to GitHub
2. Connect Railway to your GitHub repository
3. Railway will automatically detect the Python app

### Option B: Deploy with Railway CLI

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login to Railway
npx railway login

# Initialize project
npx railway init

# Deploy
npx railway up
```

## Step 4: Configure Environment Variables

In your Railway dashboard, add these environment variables:

### Required Variables
```
SECRET_KEY=your-super-secret-key-here
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
YOUTUBE_CLIENT_ID=your_youtube_client_id
YOUTUBE_CLIENT_SECRET=your_youtube_client_secret
```

### Optional Variables
```
FLASK_ENV=production
FLASK_DEBUG=False
```

**Note**: Railway automatically provides:
- `DATABASE_URL` (PostgreSQL)
- `RAILWAY_ENVIRONMENT=production`
- `RAILWAY_PUBLIC_DOMAIN=https://your-app.railway.app`
- `PORT=8080`

## Step 5: Update Spotify Redirect URI

1. After deployment, copy your Railway app URL
2. Go back to Spotify Developer Dashboard
3. Update Redirect URIs to include: `https://your-app.railway.app/spotify_callback`
4. Save changes

## Step 6: Update YouTube Redirect URI

1. Go to Google Cloud Console
2. Update OAuth 2.0 credentials
3. Add: `https://your-app.railway.app/youtube_callback`
4. Save changes

## Step 7: Test the Application

1. Visit your Railway app URL
2. Register a new account
3. Connect your Spotify account
4. Connect your YouTube account
5. Test playlist synchronization

## Features Available

### ✅ Implemented Features
- **User Authentication**: Register, login, logout
- **Platform Connection**: Connect Spotify and YouTube accounts
- **Playlist Management**: View playlists from both platforms
- **Cross-Platform Sync**: Sync playlists from YouTube to Spotify
- **Same-Platform Sync**: Sync playlists within the same platform
- **Sync Logs**: Track all synchronization activities
- **Admin Dashboard**: Monitor system usage

### 🔄 How It Works

1. **Connect Platforms**: Users connect their Spotify and YouTube accounts via OAuth
2. **Import Playlists**: The app fetches and stores playlists from both platforms
3. **Sync Songs**: Users can sync individual songs or entire playlists
4. **Cross-Platform**: Songs are searched and matched between platforms
5. **Logging**: All sync operations are logged for tracking

## Troubleshooting

### Common Issues

1. **Spotify OAuth Error**: Ensure redirect URI matches exactly
2. **YouTube API Quota**: Check your API quota in Google Cloud Console
3. **Database Connection**: Railway provides PostgreSQL automatically
4. **Token Expiration**: Tokens are refreshed automatically when possible

### Logs

View application logs in Railway dashboard:
```bash
npx railway logs
```

### Database Access

Connect to PostgreSQL database:
```bash
npx railway connect
```

## Security Notes

- Never commit `.env` files to version control
- Use strong `SECRET_KEY` in production
- Regularly rotate API keys
- Monitor API usage and quotas

## Support

For issues with:
- **Railway**: Check [Railway Documentation](https://docs.railway.app)
- **Spotify API**: Check [Spotify Web API Documentation](https://developer.spotify.com/documentation/web-api)
- **YouTube API**: Check [YouTube Data API Documentation](https://developers.google.com/youtube/v3)

## Cost Considerations

- **Railway**: Free tier available, pay-as-you-scale
- **Spotify API**: Free with rate limits
- **YouTube API**: Free with daily quota limits
- **Database**: Railway provides free PostgreSQL

## Next Steps

After successful deployment:
1. Set up custom domain (optional)
2. Configure monitoring and alerts
3. Set up automated backups
4. Consider implementing additional features like:
   - Apple Music integration
   - Playlist sharing
   - Advanced search and filtering
   - Batch operations
