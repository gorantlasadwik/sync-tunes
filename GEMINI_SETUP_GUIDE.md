# Gemini AI Setup Guide for Sync Tunes

This guide will help you set up Google's Gemini AI API for intelligent YouTube title parsing in your Sync Tunes application.

## Why Use Gemini AI?

Instead of using regex patterns to parse YouTube titles, Gemini AI can:
- **Understand context** and extract the most appropriate song name and artist
- **Handle complex titles** with movie names, multiple artists, and video descriptors
- **Provide consistent results** across different title formats
- **Improve sync success rates** when syncing from YouTube to Spotify

## Step 1: Get Gemini API Key

1. **Visit Google AI Studio**: Go to [https://makersuite.google.com/app/apikey](https://makersuite.google.com/app/apikey)

2. **Sign in** with your Google account

3. **Create API Key**:
   - Click "Create API Key"
   - Choose "Create API key in new project" or select existing project
   - Copy the generated API key

4. **Save the API Key** securely - you'll need it for the next steps

## Step 2: Add API Key to Render

### For Production (Render):

1. **Go to Render Dashboard**: [https://dashboard.render.com](https://dashboard.render.com)

2. **Select your service**: Click on your Sync Tunes service

3. **Go to Environment**: Click on "Environment" tab

4. **Add Environment Variable**:
   - **Key**: `GEMINI_API_KEY`
   - **Value**: Your Gemini API key from Step 1
   - Click "Save Changes"

5. **Redeploy**: Your service will automatically redeploy with the new environment variable

### For Development (Local):

1. **Create `.env` file** in your project root (if not exists)

2. **Add the API key**:
   ```env
   GEMINI_API_KEY=your_actual_api_key_here
   ```

3. **Restart your Flask app** to load the new environment variable

## Step 3: Test the Integration

1. **Refresh YouTube playlists** in your app to trigger the new parsing

2. **Check the logs** at `/debug_logs` to see Gemini parsing in action

3. **Look for messages like**:
   ```
   Gemini parsing: 'Badhulu Thochanai Song With Lyrics - Mr. Perfect Songs - Prabhas, Kajal Aggarwal, DSP' -> Song: 'Badhulu Thochanai', Artist: 'Mr. Perfect Songs'
   ```

## How It Works

### Without Gemini (Fallback):
- Uses regex patterns to split titles
- May not handle complex formats well
- Less accurate for movie songs and complex titles

### With Gemini AI:
- **Intelligent parsing** based on context
- **Handles complex titles** like movie songs, tributes, covers
- **Extracts clean song names** and primary artists
- **Improves Spotify search success** rates

## Example Parsing Results

| YouTube Title | Song Name | Artist |
|---------------|-----------|---------|
| `"Badhulu Thochanai Song With Lyrics - Mr. Perfect Songs - Prabhas, Kajal Aggarwal, DSP"` | `"Badhulu Thochanai"` | `"Mr. Perfect Songs"` |
| `"Tribute to Kalki 2898 Ad - Full Song \| Prabhas \| Amitabh Bachchan"` | `"Tribute to Kalki 2898 Ad"` | `"Prabhas"` |
| `"Song Name (Official Video) - Artist Name"` | `"Song Name"` | `"Artist Name"` |
| `"Movie Song - Multiple Artists - Movie Name [4K HD]"` | `"Movie Song"` | `"Multiple Artists"` |

## Troubleshooting

### If Gemini API is not working:
- Check that `GEMINI_API_KEY` is set correctly in Render
- Verify the API key is valid and active
- Check the logs for error messages
- The app will automatically fallback to regex parsing

### If parsing results are not good:
- Gemini AI learns from examples, so results may improve over time
- You can modify the prompt in `parse_youtube_title_with_gemini()` function
- The fallback regex parser will still work if needed

## Cost Considerations

- **Gemini API pricing**: Very affordable, typically $0.001-0.01 per request
- **Usage**: Only called when fetching YouTube playlists
- **Caching**: Parsed results are stored in database, so no re-parsing needed

## Security Notes

- **Never commit API keys** to version control
- **Use environment variables** for all sensitive data
- **Rotate API keys** periodically for security
- **Monitor usage** in Google AI Studio dashboard

---

**Ready to set up Gemini AI?** Follow the steps above and enjoy much better YouTube title parsing! 🚀
