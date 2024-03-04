import { createConnection } from 'mysql2/promise';
import { config } from 'dotenv';
import {getServerSession} from "next-auth";
import { RowDataPacket } from 'mysql2';



// Load environment variables for database configuration
config();

// Database configuration
const dbConfig = {
    host: process.env.DB_HOST,
    user: process.env.DB_USER,
    password: process.env.DB_PASSWORD,
    database: process.env.DB_NAME,
    port: Number(process.env.DB_PORT || 3306),
};


  
  // Assuming sesh.user is of type User
export async function POST(req: Request): Promise<Response> {
    try {
        const sesh = await getServerSession(); // Make sure to pass `req` if your session function needs it
        let email: string | null = null; // Default to null, adjust as needed

        if (sesh && sesh.user && sesh.user.email) {
            email = sesh.user.email;
        }

        // Ensure email is present
        if (!email) {
            return new Response(JSON.stringify({ error: 'No email provided' }), {
                status: 400,
                headers: { 'Content-Type': 'application/json' },
            });
        }

        const connection = await createConnection(dbConfig);

        // First, check if the user already exists
        const [users] = await connection.execute<RowDataPacket[]>(
            `SELECT * FROM users WHERE email = ?`,
            [email]
        );

        // Now you can safely check users.length because TypeScript knows users is an array
        if (users.length === 0) {
            // User does not exist, add them with default tokens
            await connection.execute(
                `INSERT INTO users (name, email, google_image_url, token_remaining, signup_date) VALUES (?, ?, ?, 5, NOW())`,
                [sesh?.user?.name, email, sesh?.user?.image]
            );
            console.log('User added with default tokens');
        } else {
            // User exists, update their tokens
            await connection.execute(
                `UPDATE users SET token_remaining = token_remaining + 5 WHERE email = ?`,
                [email]
            );
            console.log('Tokens updated for existing user');
        }

        await connection.end();
        return new Response(JSON.stringify({ message: 'Operation successful' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
        });
    } catch (error) {
        console.error(error);
        return new Response(JSON.stringify({ error: 'Failed to execute operation' }), {
            status: 500,
            headers: { 'Content-Type': 'application/json' },
        });
    }
}



export async function GET(req: Request) {
    // Example: Fetch all users
    try {
        const connection = await createConnection(dbConfig);
        const [rows] = await connection.query(`SELECT * FROM users`);
        await connection.end();
        return new Response(JSON.stringify(rows), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
        });
    } catch (error) {
        return new Response(JSON.stringify({ error: 'Failed to fetch users' }), {
            status: 500,
            headers: { 'Content-Type': 'application/json' },
        });
    }
}

export async function PUT(req: Request) {
    try {
        const sesh = await getServerSession();
        console.log(sesh);
        let email = null; // Default to null, adjust as needed
        if (sesh && sesh.user) {
            email = sesh.user.email;
            }
        const connection = await createConnection(dbConfig);
        await connection.execute(
            `UPDATE users SET token_remaining = token_remaining - 1 WHERE email = ?`,
            [email]
        );
        console.log('query executed')
        await connection.end();
        return new Response(JSON.stringify({ message: '1 token subtracted successfully' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
        });
    } catch (error) {
        return new Response(JSON.stringify({ error: 'Failed to subtract tokens' }), {
            status: 500,
            headers: { 'Content-Type': 'application/json' },
        });
    }
}