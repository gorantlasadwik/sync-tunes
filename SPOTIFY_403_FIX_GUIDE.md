# 🎵 Spotify 403 Error Fix Guide

## ❌ **Current Error:**
```
Spotify connection failed: Your account may not be registered or the app needs proper configuration. Please check your Spotify Developer Dashboard settings.
```

## 🔧 **Step-by-Step Fix:**

### **1. Check Your Spotify App Settings**

Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and click on your app:

#### **Basic App Information:**
- **App Name**: `Sync Tunes` (or any descriptive name)
- **App Description**: `Playlist synchronization between YouTube and Spotify`
- **Website**: `https://sync-tunes.onrender.com`
- **Redirect URIs**: `https://sync-tunes.onrender.com/spotify_callback`

#### **App Status:**
- Make sure your app is **NOT** in "Draft" status
- It should be either "Development" or "Live"

### **2. Verify Redirect URI**

**CRITICAL**: The redirect URI must match exactly:
```
https://sync-tunes.onrender.com/spotify_callback
```

**Common mistakes:**
- ❌ `http://` instead of `https://`
- ❌ Missing `/spotify_callback`
- ❌ Wrong domain name
- ❌ Extra spaces or characters

### **3. Check Required Scopes**

Your app needs these scopes (already configured in code):
- ✅ `playlist-read-private`
- ✅ `playlist-read-collaborative`
- ✅ `user-read-private`

### **4. Test Your Spotify Account**

Make sure your Spotify account:
- ✅ Is a **real Spotify account** (not just a developer account)
- ✅ Has **playlists** (at least one)
- ✅ Is **not restricted** or suspended

### **5. App Approval Status**

#### **For Development:**
- Your app should work with your own Spotify account
- No additional approval needed

#### **For Production (if needed later):**
- Requires Spotify's review process
- For now, stick with Development mode

## 🧪 **Testing Steps:**

### **1. Test the OAuth Flow:**
1. Go to your app: `https://sync-tunes.onrender.com`
2. Click "Connect Spotify"
3. You should be redirected to Spotify's authorization page
4. After authorization, you should be redirected back to your app

### **2. Check the Logs:**
Look for these debug messages in your app:
- `Spotify callback - user info: {...}`
- `Spotify user info: {...}`

### **3. Common Issues & Solutions:**

#### **Issue: "Invalid redirect URI"**
- **Solution**: Double-check the redirect URI in Spotify Dashboard

#### **Issue: "App not found"**
- **Solution**: Make sure the Client ID is correct in your environment variables

#### **Issue: "Invalid client"**
- **Solution**: Check Client ID and Client Secret in Render environment variables

## 🔑 **Environment Variables Check:**

Make sure these are set in your Render dashboard:
```
SPOTIFY_CLIENT_ID=135a8058b3014bc588fdb12c602f1c3a
SPOTIFY_CLIENT_SECRET=f68044395a4443928e4a278367ee7057
```

## 🚨 **If Still Not Working:**

### **Option 1: Create a New Spotify App**
1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Click "Create App"
3. Fill in the details above
4. Update your environment variables with new credentials

### **Option 2: Check App Status**
1. In your Spotify Dashboard, look for any warnings or errors
2. Make sure the app is not suspended or restricted

### **Option 3: Test with Different Account**
1. Try with a different Spotify account
2. Make sure the account has playlists

## 📞 **Need Help?**

If you're still getting 403 errors after following these steps:
1. Check the exact error message in your app logs
2. Verify all settings match exactly
3. Try creating a fresh Spotify app

---

**Remember**: The 403 error is almost always a configuration issue, not a code issue! 🎯
