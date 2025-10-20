#!/usr/bin/env python3
"""
Database initialization script for Sync Tunes
Run this script to create the database schema and initial data
Matches the Oracle schema exactly
"""

import os
import sys
from datetime import datetime
from app import app, db, Platform, Admin, User, UserPlatformAccount, Playlist
from werkzeug.security import generate_password_hash

def init_database():
    """Initialize the database with schema and initial data"""
    with app.app_context():
        print("Creating database tables...")
        db.create_all()
        
        # Create platforms if they don't exist
        print("Setting up platforms...")
        if not Platform.query.filter_by(platform_name='Spotify').first():
            spotify = Platform(
                platform_name='Spotify',
                api_details='{"api_url": "https://api.spotify.com", "version": "v1"}'
            )
            db.session.add(spotify)
            print("✓ Added Spotify platform")
        
        if not Platform.query.filter_by(platform_name='YouTube').first():
            youtube = Platform(
                platform_name='YouTube',
                api_details='{"api_url": "https://www.youtube.com", "version": "v3"}'
            )
            db.session.add(youtube)
            print("✓ Added YouTube platform")
        
        # Create admin user if it doesn't exist
        print("Setting up admin user...")
        if not Admin.query.filter_by(email='admin@synctunes.com').first():
            admin = Admin(
                name='Admin',
                email='admin@synctunes.com',
                password=generate_password_hash('admin123')
            )
            db.session.add(admin)
            print("✓ Created admin user (admin@synctunes.com / admin123)")
        else:
            print("✓ Admin user already exists")
        
        # Create demo user if it doesn't exist
        print("Setting up demo user...")
        demo_user = None
        if not User.query.filter_by(email='demo@synctunes.com').first():
            demo_user = User(
                name='Demo User',
                email='demo@synctunes.com',
                password=generate_password_hash('demo123')
            )
            db.session.add(demo_user)
            print("✓ Created demo user (demo@synctunes.com / demo123)")
        else:
            demo_user = User.query.filter_by(email='demo@synctunes.com').first()
            print("✓ Demo user already exists")
        
        # Add some demo playlists if demo user exists
        if demo_user:
            # Get platform IDs
            spotify_platform = Platform.query.filter_by(platform_name='Spotify').first()
            youtube_platform = Platform.query.filter_by(platform_name='YouTube').first()
            
            if spotify_platform and youtube_platform:
                # Create demo platform accounts
                spotify_account = UserPlatformAccount(
                    user_id=demo_user.user_id,
                    platform_id=spotify_platform.platform_id,
                    username_on_platform='demo_user_spotify',
                    auth_token='demo_token_spotify'
                )
                db.session.add(spotify_account)
                
                youtube_account = UserPlatformAccount(
                    user_id=demo_user.user_id,
                    platform_id=youtube_platform.platform_id,
                    username_on_platform='demo_user_youtube',
                    auth_token='demo_token_youtube'
                )
                db.session.add(youtube_account)
                
                # Commit to get the account IDs
                db.session.commit()
                
                # Create demo playlists
                demo_playlist1 = Playlist(
                    account_id=spotify_account.account_id,
                    name='My Favorite Rock Songs',
                    description='A collection of my favorite rock music',
                    last_updated=datetime.now().date()
                )
                db.session.add(demo_playlist1)
                
                demo_playlist2 = Playlist(
                    account_id=youtube_account.account_id,
                    name='Chill Vibes',
                    description='Relaxing music for studying',
                    last_updated=datetime.now().date()
                )
                db.session.add(demo_playlist2)
                
                print("✓ Added demo platform accounts and playlists")
        
        try:
            db.session.commit()
            print("\n✅ Database initialization completed successfully!")
            print("\nDefault accounts:")
            print("  Admin: admin@synctunes.com / admin123")
            print("  Demo:  demo@synctunes.com / demo123")
            print("\n⚠️  IMPORTANT: Change these passwords in production!")
            
        except Exception as e:
            print(f"\n❌ Error during database initialization: {e}")
            db.session.rollback()
            return False
    
    return True

def reset_database():
    """Reset the database (WARNING: This will delete all data)"""
    response = input("\n⚠️  WARNING: This will delete ALL data. Are you sure? (yes/no): ")
    if response.lower() == 'yes':
        with app.app_context():
            print("Dropping all tables...")
            db.drop_all()
            print("✓ All tables dropped")
            return init_database()
    else:
        print("Database reset cancelled.")
        return False

if __name__ == '__main__':
    print("Sync Tunes Database Initialization")
    print("=" * 40)
    
    if len(sys.argv) > 1 and sys.argv[1] == '--reset':
        reset_database()
    else:
        init_database()
    
    print("\nYou can now run the application with: python app.py")
