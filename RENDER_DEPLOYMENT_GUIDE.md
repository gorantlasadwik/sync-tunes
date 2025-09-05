# Render Deployment Guide for Sync Tunes

## 🚀 Deploy to Render (Free Alternative to Railway)

### Step 1: Prepare Your Repository

1. **Push your code to GitHub** (if not already done)
2. **Make sure all files are committed**:
   ```bash
   git add .
   git commit -m "Ready for Render deployment"
   git push origin main
   ```

### Step 2: Create Render Account

1. Go to [render.com](https://render.com)
2. Sign up with your GitHub account
3. Connect your GitHub repository

### Step 3: Deploy Web Service

1. **Click "New +"** → **"Web Service"**
2. **Connect your GitHub repo** (sync_tunes)
3. **Configure the service**:
   - **Name**: `sync-tunes`
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt && python init_db.py`
   - **Start Command**: `gunicorn app:app`
   - **Plan**: `Free`

### Step 4: Set Environment Variables

In the Render dashboard, go to **Environment** tab and add:

```
SECRET_KEY=your-super-secret-key-change-this-in-production
SPOTIFY_CLIENT_ID=your_spotify_client_id_here
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret_here
YOUTUBE_CLIENT_ID=your_youtube_client_id_here
YOUTUBE_CLIENT_SECRET=your_youtube_client_secret_here
FLASK_ENV=production
FLASK_DEBUG=False
```

### Step 5: Add PostgreSQL Database

1. **Click "New +"** → **"PostgreSQL"**
2. **Name**: `sync-tunes-db`
3. **Plan**: `Free`
4. **Copy the database URL** from the dashboard
5. **Add to environment variables**:
   ```
   DATABASE_URL=postgresql://username:password@host:port/database
   ```

### Step 6: Update Spotify Dashboard

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. **Edit your app settings**
3. **Add Redirect URI**: `https://your-app-name.onrender.com/spotify_callback`
4. **Save changes**

### Step 7: Update YouTube Dashboard

1. Go to [Google Cloud Console](https://console.developers.google.com/)
2. **Edit OAuth 2.0 credentials**
3. **Add Redirect URI**: `https://your-app-name.onrender.com/youtube_callback`
4. **Save changes**

## 🎯 Your App Will Be Available At:
`https://your-app-name.onrender.com`

## ✅ Features Included:
- ✅ **Free HTTPS** - Automatic SSL certificate
- ✅ **Free PostgreSQL** - Database included
- ✅ **Auto-deploy** - Deploys on git push
- ✅ **Custom domain** - Free subdomain
- ✅ **Logs** - View application logs
- ✅ **Metrics** - Monitor performance

## 🔧 Troubleshooting

### Common Issues:

1. **Build fails**: Check that all dependencies are in `requirements.txt`
2. **Database connection**: Ensure `DATABASE_URL` is set correctly
3. **OAuth errors**: Verify redirect URIs match exactly
4. **App crashes**: Check logs in Render dashboard

### View Logs:
- Go to your service dashboard
- Click **"Logs"** tab
- Monitor real-time logs

## 🚀 Alternative: Vercel Deployment

If you prefer Vercel:

1. **Install Vercel CLI**:
   ```bash
   npm i -g vercel
   ```

2. **Deploy**:
   ```bash
   vercel
   ```

3. **Set environment variables** in Vercel dashboard

## 📱 Next Steps After Deployment:

1. **Test Spotify OAuth** - Connect your Spotify account
2. **Test YouTube OAuth** - Connect your YouTube account  
3. **Test Playlist Sync** - Try syncing playlists
4. **Monitor Performance** - Check Render dashboard

---

**Render is the best free alternative to Railway!** 🎉
