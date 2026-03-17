import { createClient } from "redis"
import dotenv from "dotenv"
import path from "path"

dotenv.config({ path: path.resolve("../.env") })

const redis = createClient({
    username: "default",
    password: process.env.REDIS_PASSWORD,
    socket: {
        host: process.env.REDIS_HOST,
        port: Number(process.env.REDIS_PORT)
    }
})

redis.on("connect", () => {
    console.log("🧠 Redis connected (AI memory ready)")
})

redis.on("error", (err) => {
    console.error("❌ Redis Error", err)
})

await redis.connect()

export default redis