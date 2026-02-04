import { createClient, type RedisClientType } from "redis"

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

redis.on("error", (err: unknown) => {
    console.error("❌ Redis Client Error", err)
})

await redis.connect()

export default redis