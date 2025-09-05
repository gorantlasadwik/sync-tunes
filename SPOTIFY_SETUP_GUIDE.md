# Spotify API Setup Guide for Sync Tunes

## 🎵 Your Deployed Application
**HTTPS URL**: `https://synctunesspotify-production.up.railway.app`

## Step 1: Spotify Developer Dashboard Setup

### 1.1 Create Spotify App
1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Log in with your Spotify account
3. Click **"Create App"**
4. Fill in the details:
   - **App name**: `Sync Tunes`
   - **App description**: `Playlist synchronization between YouTube and Spotify`
   - **Website**: `https://synctunesspotify-production.up.railway.app`
   - **Redirect URI**: `https://synctunesspotify-production.up.railway.app/spotify_callback`
   - **API/SDKs**: Check **"Web API"**

### 1.2 Get Your Credentials
1. After creating the app, you'll see your **Client ID** and **Client Secret**
2. Copy these values - you'll need them for the next step

## Step 2: Update Railway Environment Variables

### 2.1 Update Spotify Credentials
Run these commands in your terminal to set your actual Spotify credentials:

```bash
# Replace with your actual Spotify Client ID
npx railway variables --set "SPOTIFY_CLIENT_ID=your_actual_spotify_client_id"

# Replace with your actual Spotify Client Secret  
npx railway variables --set "SPOTIFY_CLIENT_SECRET=your_actual_spotify_client_secret"
```

### 2.2 Update Secret Key (Important!)
```bash
# Generate a strong secret key for production
npx railway variables --set "SECRET_KEY=your-very-strong-secret-key-here"
```

## Step 3: YouTube API Setup (Optional)

### 3.1 Google Cloud Console Setup
1. Go to [Google Cloud Console](https://console.developers.google.com/)
2. Create a new project or select existing one
3. Enable **YouTube Data API v3**
4. Go to **Credentials** → **Create Credentials** → **OAuth 2.0 Client ID**
5. Set **Application type** to **Web application**
6. Add **Authorized redirect URIs**:
   - `https://synctunesspotify-production.up.railway.app/youtube_callback`

### 3.2 Update YouTube Credentials
```bash
# Replace with your actual YouTube credentials
npx railway variables --set "YOUTUBE_CLIENT_ID=your_actual_youtube_client_id"
npx railway variables --set "YOUTUBE_CLIENT_SECRET=your_actual_youtube_client_secret"
```

## Step 4: Deploy and Test

### 4.1 Deploy the Application
```bash
npx railway up
```

### 4.2 Test the Application
1. Visit: `https://synctunesspotify-production.up.railway.app`
2. Register a new account
3. Try connecting your Spotify account
4. Test playlist synchronization

## Step 5: Verify OAuth URLs

### 5.1 Spotify OAuth URLs
- **Redirect URI**: `https://synctunesspotify-production.up.railway.app/spotify_callback`
- **Scopes**: `playlist-read-private playlist-read-collaborative user-read-private`

### 5.2 YouTube OAuth URLs  
- **Redirect URI**: `https://synctunesspotify-production.up.railway.app/youtube_callback`
- **Scopes**: `https://www.googleapis.com/auth/youtube`

## 🔧 Troubleshooting

### Common Issues:

1. **"Invalid redirect URI"**
   - Make sure the redirect URI in Spotify Dashboard exactly matches: `https://synctunesspotify-production.up.railway.app/spotify_callback`

2. **"Client ID not found"**
   - Verify your Spotify Client ID is correctly set in Railway variables

3. **"Invalid client secret"**
   - Verify your Spotify Client Secret is correctly set in Railway variables

4. **"Access denied"**
   - Check that your Spotify app has the correct scopes enabled

### Check Environment Variables:
```bash
npx railway variables
```

### View Application Logs:
```bash
npx railway logs
```

## 🚀 Next Steps

Once Spotify is working:
1. Test playlist import from Spotify
2. Test playlist import from YouTube  
3. Test cross-platform synchronization
4. Test same-platform synchronization

## 📱 Features Available

- ✅ **Spotify OAuth Integration** - Secure login with Spotify
- ✅ **YouTube OAuth Integration** - Secure login with YouTube
- ✅ **Playlist Import** - Import playlists from both platforms
- ✅ **Cross-Platform Sync** - Sync playlists between YouTube and Spotify
- ✅ **Same-Platform Sync** - Sync playlists within the same platform
- ✅ **HTTPS Support** - Full HTTPS support for OAuth

## 🔒 Security Notes

- Never commit your actual API credentials to version control
- Use strong, unique secret keys in production
- Regularly rotate your API keys
- Monitor your API usage and quotas

---

**Your Application URL**: `https://synctunesspotify-production.up.railway.app`
